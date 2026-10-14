"""Node-to-node sync API (/api/sync/*), GED Phase 8.2.

Only ever called by the peer node's sync worker, authenticated by an HMAC
signature over the request (sync.auth.verify_node) - user sessions and API
keys are not accepted. 404 on a node without SYNC_NODE_TOKEN.

  GET  /api/sync/hello                 peer handshake: node id, role, changelog epoch
                                       + head, and how far we got with the caller's
  POST /api/sync/push                  apply the peer's changes, answer the ack cursor
  GET  /api/sync/pull?since=&limit=    our changes after `since`
  GET  /api/sync/files/info?path=      size + sha256 of a file (and of a partial upload)
  GET  /api/sync/files?path=&offset=   one chunk of a file
  PUT  /api/sync/files?path=&offset=&size=&sha256=
                                       one chunk of an upload - resumable (offset
                                       = bytes already received), verified, and
                                       never replaces a different existing file
Request and response bodies of push/pull may be gzip-compressed.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import zlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from rate_limit import limiter

from sqlalchemy import text
from sync import config, files
from sync.auth import verify_node
from sync.store import after_apply, serve_hello, serve_pull, serve_push

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Sync traffic is bursty (pages of changes, file chunks) - its own budget,
# well above the 200/minute default for user endpoints.
_RATE = "1200/minute"


def _engine():
    import ingest_router

    return ingest_router._engine()


def _json_response(request: Request, payload: dict) -> Response:
    body = json.dumps(payload, default=str, ensure_ascii=False).encode()
    if "gzip" in request.headers.get("Accept-Encoding", "") and len(body) > 1024:
        return Response(
            gzip.compress(body, 6),
            media_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )
    return Response(body, media_type="application/json")


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > config.MAX_PUSH_BYTES:
        raise HTTPException(status_code=413, detail="Sync push too large")
    if request.headers.get("Content-Encoding", "").lower() == "gzip":
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            raw = inflater.decompress(raw, config.MAX_PUSH_BYTES * 8)
        except zlib.error as exc:
            raise HTTPException(status_code=400, detail="Bad gzip body") from exc
        if inflater.unconsumed_tail:
            raise HTTPException(status_code=413, detail="Sync push too large")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Bad JSON body")
    return payload


@router.get("/hello")
@limiter.limit(_RATE)
async def hello(request: Request, peer: str = Depends(verify_node)):
    async with _engine().begin() as conn:
        return await serve_hello(conn, peer)


@router.post("/push")
@limiter.limit(_RATE)
async def push(request: Request, peer: str = Depends(verify_node)):
    payload = await _json_body(request)
    changes = payload.get("changes")
    epoch = payload.get("epoch")
    if not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f-]{36}", epoch):
        raise HTTPException(status_code=400, detail="epoch required")
    if not isinstance(changes, list) or len(changes) > config.MAX_BATCH_CHANGES:
        raise HTTPException(
            status_code=400,
            detail=f"changes must be a list of at most {config.MAX_BATCH_CHANGES}",
        )
    if any(
        isinstance(c, dict) and c.get("origin_node") not in (None, peer)
        for c in changes
    ):
        # A node only ever sends its own changes (no relaying).
        raise HTTPException(status_code=400, detail="Changes from another node")
    async with _engine().begin() as conn:
        response, result = await serve_push(conn, peer, epoch, changes)
    await after_apply(result, peer)
    return response


@router.get("/pull")
@limiter.limit(_RATE)
async def pull(
    request: Request,
    since: int = Query(0, ge=0),
    limit: int = Query(config.MAX_BATCH_CHANGES, ge=1),
    epoch: str | None = Query(None, pattern=r"^[0-9a-f-]{36}$"),
    peer: str = Depends(verify_node),
):
    async with _engine().begin() as conn:
        payload = await serve_pull(
            conn, peer, since, min(limit, config.MAX_BATCH_CHANGES), epoch
        )
    return _json_response(request, payload)


# ── Files ─────────────────────────────────────────────────────────────────────


def _file_error(exc: files.FileSyncError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/files/info")
@limiter.limit(_RATE)
async def file_info(
    request: Request, path: str = Query(...), peer: str = Depends(verify_node)
):
    try:
        return await asyncio.to_thread(files.info, path)
    except files.FileSyncError as exc:
        raise _file_error(exc) from exc


@router.get("/files")
@limiter.limit(_RATE)
async def download_chunk(
    request: Request,
    path: str = Query(...),
    offset: int = Query(0, ge=0),
    length: int = Query(config.FILE_CHUNK_BYTES, ge=1),
    peer: str = Depends(verify_node),
):
    try:
        chunk, size = await asyncio.to_thread(files.read_chunk, path, offset, length)
    except files.FileSyncError as exc:
        raise _file_error(exc) from exc
    return Response(
        chunk,
        media_type="application/octet-stream",
        headers={"X-File-Size": str(size), "X-Chunk-Offset": str(offset)},
    )


async def _destroyed(path: str) -> bool:
    async with _engine().connect() as conn:
        found = await conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM sdai_destroyed_files WHERE path = :p)"),
            {"p": path},
        )
        return bool(found.scalar())


@router.put("/files")
@limiter.limit(_RATE)
async def upload_chunk(
    request: Request,
    path: str = Query(...),
    offset: int = Query(..., ge=0),
    size: int = Query(..., ge=0),
    sha256: str = Query(..., pattern=r"^[0-9a-f]{64}$"),
    peer: str = Depends(verify_node),
):
    chunk = await request.body()
    if await _destroyed(path):
        # Destroyed here (GED Phase 6) - never recreated by a transfer.
        raise HTTPException(status_code=403, detail="File destroyed")
    try:
        return await asyncio.to_thread(
            files.write_chunk, path, offset, size, sha256, chunk
        )
    except files.FileSyncError as exc:
        raise _file_error(exc) from exc
