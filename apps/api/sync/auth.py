"""Node-to-node request signing for the /api/sync routes.

Every request carries

    X-Sync-Node       sender NODE_ID
    X-Sync-Timestamp  unix seconds
    X-Sync-Nonce      random, single use
    X-Sync-Signature  hex HMAC-SHA256(SYNC_NODE_TOKEN, canonical string)

where the canonical string is method, path with query string, node,
timestamp, nonce and the sha256 of the raw body (as sent, i.e. compressed),
one per line. A request outside the allowed clock skew or reusing a nonce
is refused. User cookies and API keys are never looked at: the sync routes
depend on verify_node only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from cache import first_in_window
from fastapi import HTTPException, Request

from sync import config

_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def canonical(
    method: str, path_qs: str, node: str, timestamp: str, nonce: str, body: bytes
) -> bytes:
    return "\n".join(
        [
            method.upper(),
            path_qs,
            node,
            timestamp,
            nonce,
            hashlib.sha256(body).hexdigest(),
        ]
    ).encode()


def sign(
    token: str,
    method: str,
    path_qs: str,
    node: str,
    body: bytes = b"",
    now: float | None = None,
) -> dict:
    """Headers for one signed request (used by the worker)."""
    timestamp = str(int(now if now is not None else time.time()))
    nonce = secrets.token_hex(16)
    signature = hmac.new(
        token.encode(),
        canonical(method, path_qs, node, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Sync-Node": node,
        "X-Sync-Timestamp": timestamp,
        "X-Sync-Nonce": nonce,
        "X-Sync-Signature": signature,
    }


def _path_qs(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


async def verify_node(request: Request) -> str:
    """FastAPI dependency: the authenticated peer's NODE_ID. 404 while sync
    is disabled on this node (no token), 401 for anything not signed with
    the shared token, a stale timestamp or a replayed nonce."""
    if not config.enabled():
        raise HTTPException(status_code=404)
    headers = request.headers
    node = headers.get("X-Sync-Node", "")
    timestamp = headers.get("X-Sync-Timestamp", "")
    nonce = headers.get("X-Sync-Nonce", "")
    signature = headers.get("X-Sync-Signature", "")
    if not (
        _NODE_RE.match(node)
        and timestamp.isdigit()
        and 16 <= len(nonce) <= 64
        and signature
    ):
        raise HTTPException(status_code=401, detail="Unsigned sync request")
    if abs(time.time() - int(timestamp)) > config.MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(
            status_code=401, detail="Sync request timestamp out of range"
        )
    # Refuse an oversized body before reading it into memory - nothing is
    # authenticated yet. Push pages and file chunks have their own caps.
    limit = max(config.MAX_PUSH_BYTES, config.MAX_FILE_CHUNK_BYTES) + 64 * 1024
    length = headers.get("Content-Length", "")
    if length and (not length.isdigit() or int(length) > limit):
        raise HTTPException(status_code=413, detail="Sync request too large")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail="Sync request too large")
    expected = hmac.new(
        config.SYNC_NODE_TOKEN.encode(),
        canonical(request.method, _path_qs(request), node, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Bad sync signature")
    # Checked after the signature so an attacker can't burn valid nonces.
    if not await first_in_window(
        f"sync-nonce:{node}:{nonce}", config.MAX_CLOCK_SKEW_SECONDS * 2
    ):
        raise HTTPException(status_code=401, detail="Replayed sync request")
    from ingest_router import NODE_ID

    if node == NODE_ID:
        raise HTTPException(
            status_code=401, detail="Sync request from this node itself"
        )
    request.state.sync_node = node
    return node
