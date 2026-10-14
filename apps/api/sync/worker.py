"""Sync worker - its own service (`python -m sync.worker`, same image as the
API). On the node that has SYNC_PEER_URL (the local one) it pushes local
changes to the peer and pulls the peer's, every SYNC_INTERVAL_SECONDS,
backing off exponentially while the peer is unreachable. On a node without
a peer URL (the cloud one) it only does housekeeping - the peer's worker
drives both directions through the sync API.

One cycle (sync_once):
  1. hello - the peer says how far it got with our changelog (it may have
     been restored or joined again: then we start over from 0)
  2. push pages of our changelog after that point, the peer acks each
  3. pull pages of the peer's changelog after our received_id, apply each
     in one transaction (sync.store.apply_from_peer)
Every step is idempotent - a cycle cut off anywhere is simply redone.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import random
import sys
import time
from typing import Protocol

from sqlalchemy import text

from sync import config, files
from sync.auth import sign
from sync.store import (
    after_apply,
    apply_from_peer,
    cursor,
    ensure_history_from,
    forget_peer,
    history_diverged,
    local_epoch,
    mark_sent,
    prune_changelog,
    read_changes,
    reseed_for,
)

# Upper bound of pages per direction in one cycle, so one cycle never runs
# for hours on a first sync - the next one continues.
MAX_PAGES_PER_CYCLE = 200
MAX_BACKOFF_SECONDS = 15 * 60
PRUNE_EVERY_SECONDS = 3600


class PeerClient(Protocol):
    async def hello(self) -> dict: ...
    async def push(self, epoch: str, changes: list[dict]) -> dict: ...
    async def pull(self, since: int, limit: int, epoch: str | None = None) -> dict: ...
    async def file_info(self, path: str) -> dict: ...
    async def upload_chunk(
        self, path: str, offset: int, size: int, sha256: str, chunk: bytes
    ) -> dict: ...
    async def download_chunk(
        self, path: str, offset: int, length: int
    ) -> tuple[bytes, int]: ...


async def push_changes(engine, client: PeerClient, peer: str, hello: dict) -> int:
    async with engine.begin() as conn:
        epoch = await local_epoch(conn)
        sent = hello["received_id"] if hello.get("received_epoch") == epoch else 0
        reason = await history_diverged(
            conn, peer, hello.get("received_epoch"), hello["received_id"]
        )
        if reason:
            await reseed_for(conn, peer, reason)
            if reason == "we_went_back":
                await forget_peer(conn, peer)
            epoch, sent = await local_epoch(conn), 0
        elif await ensure_history_from(conn, sent):
            epoch, sent = await local_epoch(conn), 0
    async with engine.begin() as conn:
        await mark_sent(conn, peer, sent)
    pushed = 0
    resets = 0
    for _ in range(MAX_PAGES_PER_CYCLE):
        async with engine.connect() as conn:
            changes = await read_changes(conn, sent, config.MAX_BATCH_CHANGES)
        if not changes:
            break
        response = await client.push(epoch, changes)
        if response.get("epoch_reset"):
            resets += 1
            if resets > 1:
                raise RuntimeError("Peer keeps resetting the push cursor")
            sent = 0
        else:
            sent = int(response["ack"])
            pushed += len(changes)
        async with engine.begin() as conn:
            await mark_sent(conn, peer, sent)
            if not response.get("epoch_reset"):
                # The peer now references these files - send them.
                await files.enqueue(conn, changes, "push")
    return pushed


async def pull_changes(engine, client: PeerClient, peer: str) -> dict:
    totals = {"pulled": 0, "applied": 0, "conflicts": 0, "errors": 0}
    async with engine.begin() as conn:
        theirs = await cursor(conn, peer)
    since = theirs["received_id"]
    known_epoch = str(theirs["peer_epoch"]) if theirs["peer_epoch"] else None
    resets = 0
    for _ in range(MAX_PAGES_PER_CYCLE):
        page = await client.pull(since, config.MAX_BATCH_CHANGES, known_epoch)
        known_epoch = page["epoch"]
        async with engine.begin() as conn:
            result, received = await apply_from_peer(
                conn, peer, page["epoch"], page["changes"]
            )
            if result is not None:
                # Rows here now reference the peer's files - fetch them.
                await files.enqueue(conn, page["changes"], "fetch")
        await after_apply(result, peer)
        if result is None:
            resets += 1
            if resets > 1:
                raise RuntimeError("Peer changelog epoch keeps changing")
            since = 0
            continue
        totals["pulled"] += len(page["changes"])
        totals["applied"] += result.applied
        totals["conflicts"] += len(result.conflicts)
        totals["errors"] += len(result.errors)
        since = max(received, int(page["last_id"]))
        if not page["has_more"]:
            break
    return totals


# ── Files ─────────────────────────────────────────────────────────────────────

MAX_FILES_PER_CYCLE = 200
MAX_FILE_BYTES_PER_CYCLE = 512 * 1024 * 1024
MAX_FILE_ATTEMPTS = 50


async def _push_file(client: PeerClient, path: str) -> tuple[str, int]:
    local = files.resolve(path)
    if not local.is_file():
        return "waiting", 0
    size = local.stat().st_size
    sha = await asyncio.to_thread(files.sha256_of, local)
    remote = await client.file_info(path)
    if remote["exists"]:
        if remote["sha256"] == sha:
            return "done", 0
        raise files.FileSyncError(
            409, "A different file exists at this path on the peer"
        )
    offset = remote.get("partial_size") or 0
    offset = offset if 0 < offset <= size else 0
    sent = 0
    restarted = False
    while True:
        chunk = await asyncio.to_thread(
            _read_at, local, offset, config.FILE_CHUNK_BYTES
        )
        try:
            response = await client.upload_chunk(path, offset, size, sha, chunk)
        except PeerError as exc:
            if exc.status in (409, 422) and not restarted:
                restarted, offset = True, 0  # out of step with the peer's part file
                continue
            raise
        sent += len(chunk)
        if response.get("complete"):
            return "done", sent
        offset = int(response["received"])


def _read_at(path, offset: int, length: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(length)


async def _fetch_file(client: PeerClient, path: str) -> tuple[str, int]:
    local = files.resolve(path)
    remote = await client.file_info(path)
    if local.is_file():
        if not remote["exists"] or remote["sha256"] == await asyncio.to_thread(
            files.sha256_of, local
        ):
            return "done", 0
        raise files.FileSyncError(409, "A different file exists at this path here")
    if not remote["exists"]:
        return "waiting", 0
    size, sha = int(remote["size"]), remote["sha256"]
    part = files.part_of(local)
    offset = part.stat().st_size if part.is_file() else 0
    offset = offset if offset <= size else 0
    received = 0
    while True:
        chunk, _ = await client.download_chunk(path, offset, config.FILE_CHUNK_BYTES)
        if not chunk and offset < size:
            raise PeerError(502, "Peer sent an empty chunk")
        result = await asyncio.to_thread(
            files.write_chunk, path, offset, size, sha, chunk
        )
        received += len(chunk)
        if result.get("complete"):
            return "done", received
        offset = int(result["received"])


async def transfer_files(engine, client: PeerClient) -> dict:
    """Move what the file queue holds, oldest first, within this cycle's
    budget. A file not on its source yet waits (retried with backoff); a
    bad path or a different file already there fails (shown to the admin).
    A network error ends the step - the next cycle resumes."""
    stats = {"files_done": 0, "files_waiting": 0, "files_failed": 0, "file_bytes": 0}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT path, direction, attempts FROM sdai_sync_file_queue q
                    WHERE status IN ('pending', 'waiting') AND next_attempt_at <= now()
                      AND NOT EXISTS (SELECT 1 FROM sdai_destroyed_files f WHERE f.path = q.path)
                    ORDER BY created_at, path LIMIT :n
                """),
                {"n": MAX_FILES_PER_CYCLE},
            )
        ).all()
    for path, direction, attempts in rows:
        if stats["file_bytes"] >= MAX_FILE_BYTES_PER_CYCLE:
            break
        error = None
        try:
            mover = _push_file if direction == "push" else _fetch_file
            outcome, nbytes = await mover(client, path)
        except files.FileSyncError as exc:
            outcome, nbytes, error = "failed", 0, str(exc.detail)
        except PeerError as exc:
            if exc.status in (400, 403):
                outcome, nbytes, error = "failed", 0, str(exc)
            elif exc.status == 404:
                outcome, nbytes = "waiting", 0
            else:
                raise
        if outcome == "waiting" and attempts + 1 >= MAX_FILE_ATTEMPTS:
            outcome, error = "failed", "File never appeared on its source node"
        stats[f"files_{outcome}"] += 1
        stats["file_bytes"] += nbytes
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE sdai_sync_file_queue
                    SET status = :s, attempts = attempts + 1, last_error = :e, updated_at = now(),
                        done_at = CASE WHEN :s = 'done' THEN now() END,
                        size = COALESCE(:b, size),
                        next_attempt_at = now() + make_interval(secs => :wait)
                    WHERE path = :p AND direction = :d
                """),
                {
                    "s": outcome,
                    "e": error,
                    "b": nbytes or None,
                    "wait": min(3600, 30 * 2 ** min(attempts, 7))
                    if outcome == "waiting"
                    else 0,
                    "p": path,
                    "d": direction,
                },
            )
    return stats


async def sync_once(engine, client: PeerClient) -> dict:
    hello = await client.hello()
    peer = hello["node_id"]
    pushed = await push_changes(engine, client, peer, hello)
    pulled = await pull_changes(engine, client, peer)
    moved = await transfer_files(engine, client)
    return {"peer": peer, "pushed": pushed, **pulled, **moved}


# ── HTTP transport ────────────────────────────────────────────────────────────


class HttpPeer:
    """Signed requests to the peer's /api/sync routes, bodies gzipped,
    throttled to SYNC_MAX_KBPS."""

    def __init__(self, base_url: str, node_id: str, token: str, max_kbps: float = 0):
        import httpx

        self._base = base_url.rstrip("/")
        self._node = node_id
        self._token = token
        self._max_kbps = max_kbps
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def _throttle(self, nbytes: int) -> None:
        if self._max_kbps > 0 and nbytes:
            await asyncio.sleep(nbytes / (self._max_kbps * 1024))

    async def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: bytes = b"",
        headers: dict | None = None,
    ):
        import httpx

        url = path + (f"?{httpx.QueryParams(params)}" if params else "")
        signed = sign(self._token, method, url, self._node, body)
        signed.update(headers or {})
        await self._throttle(len(body))
        response = await self._http.request(
            method, self._base + url, content=body, headers=signed
        )
        await self._throttle(len(response.content))
        if response.status_code >= 400:
            raise PeerError(response.status_code, response.text[:500])
        return response

    async def hello(self) -> dict:
        return (await self.request("GET", "/api/sync/hello")).json()

    async def push(self, epoch: str, changes: list[dict]) -> dict:
        body = gzip.compress(
            json.dumps({"epoch": epoch, "changes": changes}, default=str).encode(), 6
        )
        response = await self.request(
            "POST",
            "/api/sync/push",
            body=body,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        )
        return response.json()

    async def pull(self, since: int, limit: int, epoch: str | None = None) -> dict:
        params = {"since": since, "limit": limit}
        if epoch:
            params["epoch"] = epoch
        response = await self.request(
            "GET",
            "/api/sync/pull",
            params=params,
            headers={"Accept-Encoding": "gzip"},
        )
        return response.json()

    async def file_info(self, path: str) -> dict:
        return (
            await self.request("GET", "/api/sync/files/info", params={"path": path})
        ).json()

    async def upload_chunk(
        self, path: str, offset: int, size: int, sha256: str, chunk: bytes
    ) -> dict:
        response = await self.request(
            "PUT",
            "/api/sync/files",
            params={"path": path, "offset": offset, "size": size, "sha256": sha256},
            body=chunk,
            headers={"Content-Type": "application/octet-stream"},
        )
        return response.json()

    async def download_chunk(
        self, path: str, offset: int, length: int
    ) -> tuple[bytes, int]:
        response = await self.request(
            "GET",
            "/api/sync/files",
            params={"path": path, "offset": offset, "length": length},
        )
        return response.content, int(response.headers.get("X-File-Size", "0"))


class PeerError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"peer answered {status}: {detail}")
        self.status = status


# ── Status and loop ───────────────────────────────────────────────────────────


async def record_cycle(
    engine, peer: str | None, summary: dict | None, error: str | None
) -> None:
    """Worker health for the admin page: one row per peer (sdai_sync_cursors),
    plus sdai_sync_state for a peer that could not even be reached."""
    async with engine.begin() as conn:
        if error is None and peer:
            await conn.execute(
                text("""
                    UPDATE sdai_sync_cursors
                    SET last_success_at = now(), failures = 0, last_error = NULL,
                        last_summary = CAST(:s AS jsonb)
                    WHERE peer_node = :p
                """),
                {"p": peer, "s": json.dumps(summary or {})},
            )
        await conn.execute(
            text("""
                UPDATE sdai_sync_state
                SET last_cycle_at = now(),
                    last_success_at = CASE WHEN :ok THEN now() ELSE last_success_at END,
                    last_error = :e,
                    last_error_at = CASE WHEN :ok THEN last_error_at ELSE now() END,
                    failures = CASE WHEN :ok THEN 0 ELSE failures + 1 END
                WHERE id = 1
            """),
            {"ok": error is None, "e": error},
        )


def backoff_seconds(failures: int, interval: float) -> float:
    """Exponential with jitter, from the normal interval up to 15 minutes."""
    delay = min(MAX_BACKOFF_SECONDS, interval * (2 ** min(failures, 10)))
    return delay * random.uniform(0.8, 1.0)


async def run_forever() -> None:
    import ingest_router

    from sync.db import open_engine

    engine = await open_engine()
    client = None
    if config.enabled() and config.SYNC_PEER_URL:
        client = HttpPeer(
            config.SYNC_PEER_URL,
            ingest_router.NODE_ID,
            config.SYNC_NODE_TOKEN,
            config.SYNC_MAX_KBPS,
        )
        print(
            f"[sync] {ingest_router.NODE_ID} syncing with {config.SYNC_PEER_URL}",
            file=sys.stderr,
        )
    else:
        print(
            "[sync] no SYNC_PEER_URL/SYNC_NODE_TOKEN - housekeeping only",
            file=sys.stderr,
        )
    failures = 0
    last_prune = 0.0
    while True:
        delay = config.SYNC_INTERVAL_SECONDS
        if time.monotonic() - last_prune > PRUNE_EVERY_SECONDS:
            last_prune = time.monotonic()
            try:
                async with engine.begin() as conn:
                    pruned = await prune_changelog(
                        conn, config.SYNC_CHANGELOG_RETENTION_DAYS
                    )
                if pruned:
                    print(f"[sync] pruned {pruned} changelog rows", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001 - housekeeping, retried next hour
                print(f"[sync] prune failed: {exc}", file=sys.stderr)
        if client is not None:
            started = time.monotonic()
            try:
                summary = await sync_once(engine, client)
                failures = 0
                await record_cycle(engine, summary["peer"], summary, None)
                if summary["pushed"] or summary["pulled"]:
                    print(
                        f"[sync] {summary} in {time.monotonic() - started:.1f}s",
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001 - offline is normal, keep going
                failures += 1
                delay = backoff_seconds(failures, config.SYNC_INTERVAL_SECONDS)
                print(
                    f"[sync] cycle failed ({exc}); retry in {delay:.0f}s",
                    file=sys.stderr,
                )
                try:
                    await record_cycle(engine, None, None, str(exc)[:1000])
                except Exception:  # noqa: BLE001 - the database may be the problem
                    pass
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(run_forever())
