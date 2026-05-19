"""Tests for /api/ingest/* endpoints."""

import io
import pytest
from unittest.mock import AsyncMock, patch

import ingest_router
from conftest import make_result


# ── Auth guards ───────────────────────────────────────────────────────────────

def test_upload_requires_auth(client):
    resp = client.post("/api/ingest/upload",
                       files=[("files", ("doc.png", b"data", "image/png"))])
    assert resp.status_code == 401


def test_path_requires_auth(client):
    resp = client.post("/api/ingest/path", json={"path": "/tmp/docs"})
    assert resp.status_code == 401


def test_status_requires_auth(client):
    resp = client.get("/api/ingest/status/some-batch-id")
    assert resp.status_code == 401


# ── Upload: file validation ───────────────────────────────────────────────────

def test_upload_rejects_disallowed_extension(auth_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_create_batch", AsyncMock(return_value="b1"))
    resp = auth_client.post(
        "/api/ingest/upload",
        files=[("files", ("doc.exe", b"data", "application/octet-stream"))],
    )
    assert resp.status_code == 422
    assert ".exe" in resp.json()["detail"]


def test_upload_rejects_txt_file(auth_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_create_batch", AsyncMock(return_value="b1"))
    resp = auth_client.post(
        "/api/ingest/upload",
        files=[("files", ("readme.txt", b"hello", "text/plain"))],
    )
    assert resp.status_code == 422


# ── Upload: happy path ────────────────────────────────────────────────────────

def test_upload_png_success(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_router, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-abc"))
    monkeypatch.setattr(ingest_router, "_register_and_enqueue", AsyncMock())

    resp = auth_client.post(
        "/api/ingest/upload",
        files=[("files", ("scan.png", b"\x89PNG\r\n", "image/png"))],
    )

    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch-abc"
    ingest_router._create_batch.assert_awaited_once_with("upload")
    ingest_router._register_and_enqueue.assert_awaited_once()


def test_upload_pdf_success(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_router, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-pdf"))
    monkeypatch.setattr(ingest_router, "_register_and_enqueue", AsyncMock())

    resp = auth_client.post(
        "/api/ingest/upload",
        files=[("files", ("form.pdf", b"%PDF-1.4", "application/pdf"))],
    )

    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch-pdf"


def test_upload_multiple_files(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_router, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-multi"))
    monkeypatch.setattr(ingest_router, "_register_and_enqueue", AsyncMock())

    resp = auth_client.post(
        "/api/ingest/upload",
        files=[
            ("files", ("a.png", b"data", "image/png")),
            ("files", ("b.jpg", b"data", "image/jpeg")),
        ],
    )

    assert resp.status_code == 200
    assert ingest_router._register_and_enqueue.await_count == 2


# ── Path ingest ───────────────────────────────────────────────────────────────

def test_path_missing_both_params_returns_422(auth_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-p"))
    resp = auth_client.post("/api/ingest/path", json={})
    assert resp.status_code == 422


def test_path_nonexistent_directory(auth_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-p"))
    monkeypatch.setattr(ingest_router, "_register_and_enqueue", AsyncMock())

    resp = auth_client.post("/api/ingest/path",
                            json={"path": "/nonexistent/path/definitely"})
    assert resp.status_code in (404, 422)


def test_path_empty_directory(auth_client, tmp_path, monkeypatch):
    src = tmp_path / "empty_src"
    src.mkdir()
    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-p"))

    resp = auth_client.post("/api/ingest/path", json={"path": str(src)})
    assert resp.status_code == 422
    assert "No supported files" in resp.json()["detail"]


def test_path_directory_with_images(auth_client, tmp_path, monkeypatch):
    src = tmp_path / "docs"
    src.mkdir()
    (src / "scan.png").write_bytes(b"\x89PNG")
    (src / "scan2.jpg").write_bytes(b"\xff\xd8\xff")

    monkeypatch.setattr(ingest_router, "_create_batch",
                        AsyncMock(return_value="batch-p"))
    monkeypatch.setattr(ingest_router, "_register_and_enqueue", AsyncMock())

    resp = auth_client.post("/api/ingest/path", json={"path": str(src)})

    assert resp.status_code == 200
    assert resp.json()["batch_id"] == "batch-p"
    assert ingest_router._register_and_enqueue.await_count == 2


# ── Status ────────────────────────────────────────────────────────────────────

def test_status_batch_not_found(auth_client, monkeypatch):
    session = _make_fake_session(monkeypatch, fetchall=[])

    resp = auth_client.get("/api/ingest/status/nonexistent-batch")
    assert resp.status_code == 404


def test_status_returns_batch_progress(auth_client, monkeypatch):
    import uuid as _uuid
    doc_id = _uuid.uuid4()
    rows = [
        (doc_id, "scan.png", "completed", "ordre_de_mission", "high", 1.5, None, "/data/img.png"),
    ]
    _make_fake_session(monkeypatch, fetchall=rows)

    resp = auth_client.get("/api/ingest/status/some-batch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["documents"][0]["status"] == "completed"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_session(monkeypatch, *, fetchall):
    """Patch ingest_router.SessionLocal to return rows on fetchall()."""
    from unittest.mock import MagicMock

    result = MagicMock()
    result.fetchall.return_value = fetchall

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit  = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__  = AsyncMock(return_value=False)

    monkeypatch.setattr(ingest_router, "SessionLocal",
                        MagicMock(return_value=session_ctx))
    return session
