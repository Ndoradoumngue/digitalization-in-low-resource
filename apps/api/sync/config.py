"""Sync settings (see .env.example, "Local/cloud sync")."""

import logging
import os
from pathlib import Path

# Shared secret both nodes sign their sync requests with. Unset = sync
# disabled: the /api/sync routes answer 404 and the worker idles.
SYNC_NODE_TOKEN = os.getenv("SYNC_NODE_TOKEN", "")
if SYNC_NODE_TOKEN and len(SYNC_NODE_TOKEN) < 32:
    # Whoever guesses it can push changes into this node's archive.
    logging.getLogger(__name__).warning(
        "SYNC_NODE_TOKEN is shorter than 32 characters - generate one with"
        " `openssl rand -hex 32` (the same value on both nodes)"
    )
# Set on the node that initiates sync (the local one - it can reach the
# cloud, not the other way round). Empty on the cloud node.
SYNC_PEER_URL = os.getenv("SYNC_PEER_URL", "").rstrip("/")
SYNC_INTERVAL_SECONDS = float(os.getenv("SYNC_INTERVAL_SECONDS", "30"))
# Upload/download bandwidth cap for the worker, KB/s. 0 = unlimited.
SYNC_MAX_KBPS = float(os.getenv("SYNC_MAX_KBPS", "0"))
# Changelog rows every peer has applied are deleted after this many days.
# A peer asking for older rows (restored from an older backup, re-joined)
# gets a fresh snapshot instead (reseed).
# The admin page alerts when sync has not succeeded for this many hours.
SYNC_ALERT_HOURS = float(os.getenv("SYNC_ALERT_HOURS", "6"))
SYNC_CHANGELOG_RETENTION_DAYS = float(os.getenv("SYNC_CHANGELOG_RETENTION_DAYS", "30"))

# Signed requests older or newer than this are refused (replays, clock
# skew - keep both nodes on NTP).
MAX_CLOCK_SKEW_SECONDS = 300
# One push/pull page: at most this many changes, and a push body of at
# most this many bytes (compressed).
MAX_BATCH_CHANGES = int(os.getenv("SYNC_BATCH_CHANGES", "500"))
MAX_PUSH_BYTES = int(os.getenv("SYNC_MAX_PUSH_BYTES", str(32 * 1024 * 1024)))
# One file upload/download chunk.
FILE_CHUNK_BYTES = 1024 * 1024
MAX_FILE_CHUNK_BYTES = 8 * 1024 * 1024


def enabled() -> bool:
    return bool(SYNC_NODE_TOKEN)


def file_roots() -> list[Path]:
    """Directories files may be copied into or out of between nodes - the
    same absolute paths on both nodes (deployment requirement, see
    README "Document management (GED)"): page images, uploads (incl.
    versions), the documents/ volume, the server-path ingest root and
    generated files."""
    import ingest_router

    return [
        ingest_router.IMAGES_DIR.resolve(),
        ingest_router.UPLOADS_DIR.resolve(),
        ingest_router.DOCS_DIR.resolve(),
        ingest_router.INGEST_ROOT.resolve(),
        (ingest_router.DATA_DIR / "generated").resolve(),
    ]
