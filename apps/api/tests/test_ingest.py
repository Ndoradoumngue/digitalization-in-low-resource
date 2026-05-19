"""Tests for /api/ingest/* endpoints."""

import asyncio
import hashlib
import io
import uuid as _uuid_mod

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── _compute_hash ─────────────────────────────────────────────────────────────

def test_compute_hash_matches_hashlib(tmp_path):
    content = b"known content for sha256 test"
    f = tmp_path / "test.bin"
    f.write_bytes(content)
    assert ingest_router._compute_hash(f) == hashlib.sha256(content).hexdigest()


def test_compute_hash_is_deterministic(tmp_path):
    f = tmp_path / "repeat.bin"
    f.write_bytes(b"repeatable content")
    assert ingest_router._compute_hash(f) == ingest_router._compute_hash(f)


def test_compute_hash_differs_for_different_content(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"content A")
    b.write_bytes(b"content B")
    assert ingest_router._compute_hash(a) != ingest_router._compute_hash(b)


# ── _find_duplicate ───────────────────────────────────────────────────────────

def test_find_duplicate_no_tables_returns_none(mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    assert asyncio.run(ingest_router._find_duplicate("a" * 64)) is None


def test_find_duplicate_no_match_returns_none(mock_db):
    mock_db.execute.side_effect = [
        make_result(rows=[("some_table",)]),
        make_result(one_or_none=None),
    ]
    assert asyncio.run(ingest_router._find_duplicate("b" * 64)) is None


def test_find_duplicate_returns_matching_record(mock_db):
    the_uuid = "12345678-1234-5678-1234-567812345678"
    mock_db.execute.side_effect = [
        make_result(rows=[("some_table",)]),
        make_result(one_or_none=(the_uuid,)),
    ]
    result = asyncio.run(ingest_router._find_duplicate("c" * 64))
    assert result == {"table_name": "some_table", "id": the_uuid}


def test_find_duplicate_stops_at_first_match(mock_db):
    uuid_b = "bbbbbbbb-0000-0000-0000-000000000002"
    mock_db.execute.side_effect = [
        make_result(rows=[("table_a",), ("table_b",)]),
        make_result(one_or_none=None),          # table_a: no match
        make_result(one_or_none=(uuid_b,)),     # table_b: match
    ]
    result = asyncio.run(ingest_router._find_duplicate("d" * 64))
    assert result == {"table_name": "table_b", "id": uuid_b}
    assert mock_db.execute.await_count == 3


# ── _register_and_enqueue ─────────────────────────────────────────────────────

def test_register_and_enqueue_duplicate_skips_queue(monkeypatch, tmp_path):
    the_hash = "a" * 64
    monkeypatch.setattr(ingest_router, "_compute_hash", lambda p: the_hash)
    monkeypatch.setattr(
        ingest_router, "_find_duplicate",
        AsyncMock(return_value={"table_name": "orders", "id": "dup-id"}),
    )
    session = _make_fake_session(monkeypatch, fetchall=[])

    queue_mock = MagicMock()
    queue_mock.put = AsyncMock()
    monkeypatch.setattr(ingest_router, "_queue", queue_mock)

    src = tmp_path / "dup.png"
    src.write_bytes(b"\x89PNG")
    asyncio.run(ingest_router._register_and_enqueue("batch-dup", src, "dup.png"))

    queue_mock.put.assert_not_awaited()
    first_sql = str(session.execute.call_args_list[0][0][0])
    assert "duplicate" in first_sql


def test_register_and_enqueue_normal_path_enqueues_with_hash(monkeypatch, tmp_path):
    the_hash = "b" * 64
    doc_id   = str(_uuid_mod.uuid4())

    monkeypatch.setattr(ingest_router, "_compute_hash", lambda p: the_hash)
    monkeypatch.setattr(ingest_router, "_find_duplicate", AsyncMock(return_value=None))

    result_mock = MagicMock()
    result_mock.scalar.return_value = doc_id
    session = MagicMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.commit  = AsyncMock()
    sess_ctx = MagicMock()
    sess_ctx.__aenter__ = AsyncMock(return_value=session)
    sess_ctx.__aexit__  = AsyncMock(return_value=False)
    monkeypatch.setattr(ingest_router, "SessionLocal", MagicMock(return_value=sess_ctx))

    queue_mock = MagicMock()
    queue_mock.put = AsyncMock()
    monkeypatch.setattr(ingest_router, "_queue", queue_mock)

    src = tmp_path / "new.png"
    src.write_bytes(b"\x89PNG")
    asyncio.run(ingest_router._register_and_enqueue("batch-new", src, "new.png"))

    queue_mock.put.assert_awaited_once()
    enqueued = queue_mock.put.call_args[0][0]
    assert enqueued[4] == the_hash


# ── duplicates_skipped in batch status ────────────────────────────────────────

def test_status_includes_duplicates_skipped(auth_client, monkeypatch):
    rows = [
        (_uuid_mod.uuid4(), "doc1.png", "duplicate", None, None, None, None, None),
        (_uuid_mod.uuid4(), "doc2.png", "duplicate", None, None, None, None, None),
        (_uuid_mod.uuid4(), "doc3.png", "completed", "ordre_de_mission", "high", 1.5, None, "/img.png"),
    ]
    _make_fake_session(monkeypatch, fetchall=rows)

    resp = auth_client.get("/api/ingest/status/some-batch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicates_skipped"] == 2
    assert body["total"] == 3
    assert body["completed"] == 1


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
