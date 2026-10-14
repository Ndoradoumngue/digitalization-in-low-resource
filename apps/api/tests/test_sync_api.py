"""Node-to-node sync API: HMAC node authentication and file transfer
(GED Phase 8.2). No database - the change push/pull paths are covered
against a real PostgreSQL in test_sync_pg.py."""

import hashlib
import time
from urllib.parse import urlencode

import api_server
import pytest
from fastapi.testclient import TestClient
from sync import auth as sync_auth
from sync import config as sync_config

TOKEN = "test-sync-token"
PEER = "cloud-1"


@pytest.fixture()
def client(dirs, monkeypatch, tmp_path):
    monkeypatch.setattr(sync_config, "SYNC_NODE_TOKEN", TOKEN)
    root = tmp_path / "synced"
    root.mkdir()
    monkeypatch.setattr(sync_config, "file_roots", lambda: [root.resolve()])
    # No database here: nothing was destroyed (GED Phase 6 check).
    import sync.api

    async def _never(path):
        return False

    monkeypatch.setattr(sync.api, "_destroyed", _never)
    with TestClient(api_server.app) as c:
        c.root = root.resolve()
        yield c


def signed(
    client,
    method,
    path,
    *,
    params=None,
    body=b"",
    node=PEER,
    token=TOKEN,
    now=None,
    headers=None,
):
    url = path + ("?" + urlencode(params) if params else "")
    h = sync_auth.sign(token, method, url, node, body, now=now)
    h.update(headers or {})
    return client.request(method, url, content=body, headers=h)


# ── Authentication ───────────────────────────────────────────────────────────


def test_disabled_without_token(dirs, monkeypatch):
    monkeypatch.setattr(sync_config, "SYNC_NODE_TOKEN", "")
    with TestClient(api_server.app) as c:
        assert (
            signed(c, "GET", "/api/sync/files/info", params={"path": "/x"}).status_code
            == 404
        )


def test_unsigned_request_rejected(client):
    assert client.get("/api/sync/files/info", params={"path": "/x"}).status_code == 401


def test_user_cookie_is_not_enough(client):
    client.cookies.set("access_token", "Bearer whatever")
    r = client.get("/api/sync/pull")
    assert r.status_code == 401


def test_wrong_token_rejected(client):
    r = signed(
        client,
        "GET",
        "/api/sync/files/info",
        params={"path": str(client.root / "a")},
        token="other",
    )
    assert r.status_code == 401


def test_stale_timestamp_rejected(client):
    old = time.time() - sync_config.MAX_CLOCK_SKEW_SECONDS - 5
    r = signed(
        client,
        "GET",
        "/api/sync/files/info",
        params={"path": str(client.root / "a")},
        now=old,
    )
    assert r.status_code == 401


def test_replayed_nonce_rejected(client):
    url = "/api/sync/files/info?" + urlencode({"path": str(client.root / "a")})
    headers = sync_auth.sign(TOKEN, "GET", url, PEER)
    assert client.get(url, headers=headers).status_code == 200
    assert client.get(url, headers=headers).status_code == 401


def test_signature_covers_query_and_body(client):
    url = "/api/sync/files/info?" + urlencode({"path": str(client.root / "a")})
    headers = sync_auth.sign(TOKEN, "GET", url, PEER)
    tampered = "/api/sync/files/info?" + urlencode({"path": str(client.root / "b")})
    assert client.get(tampered, headers=headers).status_code == 401

    data = b"hello"
    params = {
        "path": str(client.root / "f.bin"),
        "offset": 0,
        "size": 5,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    url = "/api/sync/files?" + urlencode(params)
    headers = sync_auth.sign(TOKEN, "PUT", url, PEER, data)
    assert client.put(url, content=b"HELLO", headers=headers).status_code == 401


def test_request_claiming_to_be_this_node_rejected(client):
    r = signed(
        client,
        "GET",
        "/api/sync/files/info",
        params={"path": str(client.root / "a")},
        node="local",
    )
    assert r.status_code == 401


# ── Files ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", ["relative/file.png", "/etc/passwd", "/tmp/../etc/passwd"]
)
def test_paths_outside_roots_refused(client, path):
    r = signed(client, "GET", "/api/sync/files/info", params={"path": path})
    assert r.status_code in (400, 403)


def test_traversal_out_of_root_refused(client):
    r = signed(
        client,
        "GET",
        "/api/sync/files/info",
        params={"path": str(client.root / ".." / "x")},
    )
    assert r.status_code == 403


def _put(client, path, data, offset, size, sha):
    return signed(
        client,
        "PUT",
        "/api/sync/files",
        params={"path": str(path), "offset": offset, "size": size, "sha256": sha},
        body=data,
    )


def test_chunked_upload_resumes_and_verifies(client):
    data = bytes(range(256)) * 10
    sha = hashlib.sha256(data).hexdigest()
    target = client.root / "uploads" / "b1" / "doc.pdf"

    r = _put(client, target, data[:1000], 0, len(data), sha)
    assert r.json() == {"received": 1000, "complete": False}
    info = signed(
        client, "GET", "/api/sync/files/info", params={"path": str(target)}
    ).json()
    assert info["exists"] is False and info["partial_size"] == 1000

    # A chunk at the wrong offset is refused with where to resume from.
    r = _put(client, target, data[1500:], 1500, len(data), sha)
    assert r.status_code == 409 and r.json()["detail"]["resume_from"] == 1000

    r = _put(client, target, data[1000:], 1000, len(data), sha)
    assert r.json() == {"received": len(data), "complete": True}
    assert target.read_bytes() == data
    assert not (target.parent / "doc.pdf.sync-part").exists()

    info = signed(
        client, "GET", "/api/sync/files/info", params={"path": str(target)}
    ).json()
    assert info == {
        "path": str(target),
        "exists": True,
        "size": len(data),
        "sha256": sha,
        "partial_size": 0,
    }

    # Same content again: fine. Different content: never overwritten.
    assert _put(client, target, data, 0, len(data), sha).json()["complete"] is True
    other = b"different"
    r = _put(client, target, other, 0, len(other), hashlib.sha256(other).hexdigest())
    assert r.status_code == 409
    assert target.read_bytes() == data


def test_checksum_mismatch_discards_upload(client):
    target = client.root / "x.png"
    r = _put(client, target, b"abc", 0, 3, "0" * 64)
    assert r.status_code == 422
    assert not target.exists() and not (client.root / "x.png.sync-part").exists()


def test_part_files_are_not_addressable(client):
    r = signed(
        client,
        "GET",
        "/api/sync/files",
        params={"path": str(client.root / "x.png.sync-part")},
    )
    assert r.status_code == 400


def test_download_in_chunks(client):
    data = b"0123456789" * 100
    target = client.root / "images" / "p1.png"
    target.parent.mkdir()
    target.write_bytes(data)
    first = signed(
        client,
        "GET",
        "/api/sync/files",
        params={"path": str(target), "offset": 0, "length": 600},
    )
    rest = signed(
        client,
        "GET",
        "/api/sync/files",
        params={"path": str(target), "offset": 600, "length": 600},
    )
    assert first.headers["X-File-Size"] == str(len(data))
    assert first.content + rest.content == data
    missing = signed(
        client, "GET", "/api/sync/files", params={"path": str(client.root / "nope.png")}
    )
    assert missing.status_code == 404


# ── File queue helpers ────────────────────────────────────────────────────────


def test_paths_in_change_picks_file_columns_only():
    from sync.files import paths_in

    change = {
        "op": "insert",
        "row_data": {
            "source_image_path": "/data/images/a.png",
            "page_image_paths": ["/data/images/a.png", "/data/images/b.png", 3],
            "original_path": "relative/not-a-file.pdf",
            "reference_number": "/data/looks/like/a/path",
            "file_path": None,
        },
    }
    assert paths_in(change) == [
        "/data/images/a.png",
        "/data/images/a.png",
        "/data/images/b.png",
    ]
    assert paths_in({"op": "delete", "row_data": None}) == []
    assert (
        paths_in({"op": "ddl", "row_data": {"columns": {"image_path": "text"}}}) == []
    )


def test_chroot_keeps_paths_inside_their_root(tmp_path, monkeypatch):
    from pathlib import Path

    from sync import files

    monkeypatch.setattr(files, "CHROOT", tmp_path)
    monkeypatch.setattr(sync_config, "file_roots", lambda: [Path("/data")])
    assert (
        files.resolve("/data/images/x.png")
        == (tmp_path / "data/images/x.png").resolve()
    )
    for bad in ("/data/../etc/passwd", "/other/x.png", "/data/x.png.sync-part"):
        with pytest.raises(files.FileSyncError):
            files.resolve(bad)


# ── Admin routes ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/admin/sync/status"),
        ("GET", "/api/admin/sync/conflicts"),
        ("GET", "/api/admin/sync/files"),
        ("POST", "/api/admin/sync/files/retry"),
        ("POST", "/api/admin/sync/conflicts/00000000-0000-0000-0000-000000000000/resolve"),
    ],
)
def test_sync_admin_routes_are_admin_only(dirs, method, path):
    from auth import get_current_user
    from conftest import ACCESS_MANAGER, READER, REVIEWER

    with TestClient(api_server.app) as c:
        for user in (REVIEWER, READER, ACCESS_MANAGER):
            api_server.app.dependency_overrides[get_current_user] = lambda u=user: u
            try:
                r = c.request(method, path, json={"action": "dismiss"})
            finally:
                api_server.app.dependency_overrides.pop(get_current_user, None)
            assert r.status_code == 403, (user.role, path)


def test_oversized_body_refused_before_reading(client, monkeypatch):
    """GED Phase 9: an unauthenticated caller can't make the node buffer an
    arbitrarily large body - refused on its declared length."""
    monkeypatch.setattr(sync_config, "MAX_PUSH_BYTES", 1024)
    monkeypatch.setattr(sync_config, "MAX_FILE_CHUNK_BYTES", 1024)
    body = b"x" * (70 * 1024)
    assert signed(client, "POST", "/api/sync/push", body=body).status_code == 413
    resp = client.post(
        "/api/sync/push", content=b"", headers={"Content-Length": "999999999"}
    )
    assert resp.status_code in (401, 413)


# ── python -m sync.check (GED Phase 9 first-run script) ─────────────────────


def test_sync_check_reports_the_peer_or_the_cause(monkeypatch, capsys):
    import asyncio

    import sync.worker
    from sync import check

    class FakePeer:
        outcome: object = {"node_id": "cloud-1", "node_role": "cloud"}

        def __init__(self, *args, **kwargs):
            pass

        async def hello(self):
            if isinstance(FakePeer.outcome, Exception):
                raise FakePeer.outcome
            return FakePeer.outcome

        async def close(self):
            pass

    monkeypatch.setattr(sync.worker, "HttpPeer", FakePeer)
    monkeypatch.setattr(sync_config, "SYNC_NODE_TOKEN", TOKEN)
    monkeypatch.setattr(sync_config, "SYNC_PEER_URL", "https://ged.example.org")
    assert asyncio.run(check.check()) == 0
    assert "reached cloud-1" in capsys.readouterr().out

    FakePeer.outcome = sync.worker.PeerError(401, "Bad sync signature")
    assert asyncio.run(check.check()) == 1
    assert "SYNC_NODE_TOKEN differs" in capsys.readouterr().err

    FakePeer.outcome = ConnectionError("refused")
    assert asyncio.run(check.check()) == 1
    assert "cannot reach https://ged.example.org" in capsys.readouterr().err

    # the cloud node has no peer to call
    monkeypatch.setattr(sync_config, "SYNC_PEER_URL", "")
    assert asyncio.run(check.check()) == 0
