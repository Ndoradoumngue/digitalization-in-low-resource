"""Files between nodes (GED Phase 8.5).

Files stay on each node's own disk, at the SAME absolute paths (deployment
requirement), and are copied through the sync API: the file columns of a
synced row (FILE_COLUMNS) name the files that must exist on both nodes.
The initiating node keeps the queue (sdai_sync_file_queue): files named by
the changes it pushes are pushed (it has them), files named by the changes
it pulls are fetched (the peer has them). Transfers are chunked, resumable
(a .sync-part file grows until complete), sha256-verified, and never
replace an existing file with different content - files are never
overwritten, a change is always a new file.

The chunk primitives here are shared by the /api/sync/files routes and the
worker's own side of a transfer.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import text

from sync import config

# Columns of synced rows that hold a file path (absolute, under the file
# roots) - per-type document tables, uploads, pages, versions.
FILE_COLUMNS = frozenset(
    {
        "source_image_path",
        "source_pdf_path",
        "original_path",
        "source_path",
        "image_path",
        "file_path",
    }
)
# Columns holding a JSON list of file paths.
LIST_FILE_COLUMNS = frozenset({"page_image_paths"})

PART_SUFFIX = ".sync-part"

# Tests only: one machine playing two nodes gives each its own "disk" by
# re-rooting every absolute path under this directory.
CHROOT: Path | None = None


class FileSyncError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def _real(logical: Path) -> Path:
    return CHROOT / logical.relative_to("/") if CHROOT else logical


def local_path(raw: str) -> Path:
    """Where a path named in a synced row is on this node's disk (itself,
    outside tests)."""
    return _real(Path(os.path.normpath(raw)))


def resolve(raw: str) -> Path:
    """The on-disk path for a requested file, if it lies inside one of the
    sync file roots. `..` is normalized away and symlinks are followed
    before the check, so neither can step outside."""
    if not isinstance(raw, str) or not raw or not os.path.isabs(raw) or "\x00" in raw:
        raise FileSyncError(400, "Bad path")
    if raw.endswith(PART_SUFFIX):
        raise FileSyncError(400, "Bad path")
    logical = Path(os.path.normpath(raw))
    roots = [r for r in config.file_roots() if logical.is_relative_to(r)]
    if not roots:
        raise FileSyncError(403, "Path outside the synced roots")
    real = _real(logical).resolve()
    if not any(real.is_relative_to(_real(r).resolve()) for r in roots):
        raise FileSyncError(403, "Path outside the synced roots")
    return real


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def part_of(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


def info(raw: str) -> dict:
    target = resolve(raw)
    part = part_of(target)
    result = {
        "path": raw,
        "exists": target.is_file(),
        "size": None,
        "sha256": None,
        "partial_size": 0,
    }
    if result["exists"]:
        result["size"] = target.stat().st_size
        result["sha256"] = sha256_of(target)
    elif part.is_file():
        result["partial_size"] = part.stat().st_size
    return result


def read_chunk(raw: str, offset: int, length: int) -> tuple[bytes, int]:
    target = resolve(raw)
    if not target.is_file():
        raise FileSyncError(404, "No such file")
    with open(target, "rb") as fh:
        fh.seek(offset)
        return fh.read(min(length, config.MAX_FILE_CHUNK_BYTES)), os.fstat(
            fh.fileno()
        ).st_size


def write_chunk(raw: str, offset: int, size: int, sha256: str, chunk: bytes) -> dict:
    """Append one chunk of a file being received. `offset` must be what has
    been received so far (0 restarts); the last chunk is verified against
    `sha256` and only then becomes the file - via link(), which refuses an
    existing target, so a file is never replaced (same content is fine,
    different content is 409)."""
    target = resolve(raw)
    if len(chunk) > config.MAX_FILE_CHUNK_BYTES or offset + len(chunk) > size:
        raise FileSyncError(413, "Chunk too large")
    if target.exists():
        if sha256_of(target) == sha256:
            return {"received": size, "complete": True}
        raise FileSyncError(409, "A different file already exists at this path")
    part = part_of(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if offset and offset != have:
        raise FileSyncError(
            409, {"received": have, "complete": False, "resume_from": have}
        )
    with open(part, "r+b" if offset else "wb") as fh:
        fh.seek(offset)
        fh.write(chunk)
        fh.truncate()
    received = offset + len(chunk)
    if received < size:
        return {"received": received, "complete": False}
    if sha256_of(part) != sha256:
        part.unlink(missing_ok=True)
        raise FileSyncError(422, "Checksum mismatch - upload restarted")
    try:
        os.link(part, target)
    except FileExistsError:
        same = sha256_of(target) == sha256
        part.unlink(missing_ok=True)
        if not same:
            raise FileSyncError(
                409, "A different file already exists at this path"
            ) from None
        return {"received": received, "complete": True}
    part.unlink(missing_ok=True)
    return {"received": received, "complete": True}


# ── Queue ─────────────────────────────────────────────────────────────────────


def paths_in(change: dict) -> list[str]:
    """File paths named by one change (insert/update data)."""
    data = change.get("row_data") if isinstance(change, dict) else None
    if not isinstance(data, dict) or change.get("op") not in ("insert", "update"):
        return []
    found = []
    for col, value in data.items():
        if col in FILE_COLUMNS and isinstance(value, str):
            found.append(value)
        elif col in LIST_FILE_COLUMNS and isinstance(value, list):
            found.extend(v for v in value if isinstance(v, str))
    return [p for p in found if p and os.path.isabs(p)]


async def enqueue(conn, changes: list[dict], direction: str) -> int:
    """Queue every file the changes name, once per path and direction (a
    path already transferred stays done - files never change)."""
    rows = {}
    for change in changes:
        for path in paths_in(change):
            rows[path] = change.get("table_name")
    for path, table in rows.items():
        await conn.execute(
            text("""
                INSERT INTO sdai_sync_file_queue (path, direction, table_name)
                VALUES (:p, :d, :t)
                ON CONFLICT (path, direction) DO NOTHING
            """),
            {"p": path, "d": direction, "t": table},
        )
    return len(rows)


async def is_pending(conn, path: str) -> bool:
    """A referenced file not transferred yet (for the UI: "file being
    synchronised" instead of "missing")."""
    return bool(
        (
            await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sdai_sync_file_queue WHERE path = :p AND status <> 'done')"
                ),
                {"p": path},
            )
        ).scalar()
    )


async def file_pending(conn, path: str) -> bool:
    """A file a row references but this node does not have yet, because
    sync has not brought it over: on the cloud node (which never initiates
    transfers) any missing file while sync is on, on the local node a file
    still in its queue."""
    if not config.enabled():
        return False
    import ingest_router

    return ingest_router.NODE_ROLE == "cloud" or await is_pending(conn, path)


def pending_exception(locale: str):
    from fastapi import HTTPException
    from i18n import t

    return HTTPException(
        status_code=404,
        detail=t("sync.file_pending", locale),
        headers={"X-Sync-Pending": "1"},
    )
