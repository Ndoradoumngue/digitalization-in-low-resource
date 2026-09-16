"""Tests for /api/ingest/* endpoints."""

import asyncio
import hashlib
import io
import uuid as _uuid_mod

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import ingest_router
from conftest import make_result, REVIEWER, TENANT_ID, TENANT_SLUG


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
    ingest_router._create_batch.assert_awaited_once_with(
        "upload", REVIEWER.tenant_id, created_by=REVIEWER.id
    )
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


# ── _next_record_id ───────────────────────────────────────────────────────────

def test_next_record_id_format(mock_db):
    import datetime as _dt
    mock_db.execute.return_value = make_result(scalar=42)
    result = asyncio.run(ingest_router._next_record_id(mock_db, "tenant-1", "land"))
    year = _dt.datetime.now(_dt.timezone.utc).year
    assert result == f"LAND-{year}-000042"


def test_next_record_id_uppercases_slug_and_zero_pads(mock_db):
    mock_db.execute.return_value = make_result(scalar=7)
    result = asyncio.run(ingest_router._next_record_id(mock_db, "tenant-1", "kabalay_lexicon"))
    assert result.startswith("KABALAY_LEXICON-")
    assert result.endswith("-000007")


# ── _find_duplicate ───────────────────────────────────────────────────────────

def test_find_duplicate_no_tables_returns_none(mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    assert asyncio.run(ingest_router._find_duplicate("a" * 64, TENANT_SLUG)) is None


def test_find_duplicate_no_match_returns_none(mock_db):
    mock_db.execute.side_effect = [
        make_result(rows=[("some_table",)]),
        make_result(one_or_none=None),
    ]
    assert asyncio.run(ingest_router._find_duplicate("b" * 64, TENANT_SLUG)) is None


def test_find_duplicate_returns_matching_record(mock_db):
    the_uuid = "12345678-1234-5678-1234-567812345678"
    mock_db.execute.side_effect = [
        make_result(rows=[("some_table",)]),
        make_result(one_or_none=(the_uuid,)),
    ]
    result = asyncio.run(ingest_router._find_duplicate("c" * 64, TENANT_SLUG))
    assert result == {"table_name": "some_table", "id": the_uuid}


def test_find_duplicate_stops_at_first_match(mock_db):
    uuid_b = "bbbbbbbb-0000-0000-0000-000000000002"
    mock_db.execute.side_effect = [
        make_result(rows=[("table_a",), ("table_b",)]),
        make_result(one_or_none=None),          # table_a: no match
        make_result(one_or_none=(uuid_b,)),     # table_b: match
    ]
    result = asyncio.run(ingest_router._find_duplicate("d" * 64, TENANT_SLUG))
    assert result == {"table_name": "table_b", "id": uuid_b}
    assert mock_db.execute.await_count == 3


# ── private-by-default after review: uploaded_by / _grant_uploader_access ────

def test_grant_uploader_access_noop_when_uploaded_by_none(mock_db):
    """No known uploader (in practice: a document that predates this
    feature) — must not touch the DB at all, and must leave the document
    open (no grant means visible to everyone, today's behavior)."""
    asyncio.run(ingest_router._grant_uploader_access(mock_db, TENANT_ID, "my_table", "doc-1", None))
    mock_db.execute.assert_not_awaited()


def test_grant_uploader_access_inserts_grant(mock_db):
    mock_db.execute.return_value = make_result()
    asyncio.run(ingest_router._grant_uploader_access(mock_db, TENANT_ID, "my_table", "doc-1", "user-1"))
    mock_db.execute.assert_awaited_once()
    sql = str(mock_db.execute.call_args[0][0])
    assert "INSERT INTO sdai_document_access" in sql
    assert "'user'" in sql


def test_store_extraction_grants_uploader_when_auto_approved(mock_db, monkeypatch):
    """High-confidence documents skip review_required and land as
    auto_approved directly at ingest — privacy must apply immediately,
    not wait for a separate review action."""
    monkeypatch.setattr(ingest_router, "_ensure_table", AsyncMock(return_value="unchanged"))
    monkeypatch.setattr(ingest_router, "_next_record_id", AsyncMock(return_value="DEFAULT-2026-000001"))
    mock_db.execute.side_effect = [
        make_result(scalar="uploader-uuid-1"),  # SELECT created_by FROM batches
        make_result(scalar="doc-uuid-1"),       # INSERT ... RETURNING id
        make_result(),                          # INSERT sdai_document_access (grant)
        make_result(),                          # log_action's audit INSERT
    ]
    asyncio.run(ingest_router._store_extraction(
        "batch-1", "/img.png", {"document_type": "arrete"}, "high", "auto_approved",
        TENANT_ID, TENANT_SLUG,
    ))
    assert mock_db.execute.await_count == 4
    grant_sql = str(mock_db.execute.call_args_list[2][0][0])
    assert "INSERT INTO sdai_document_access" in grant_sql


def test_store_extraction_no_grant_when_review_required(mock_db, monkeypatch):
    """A document still in the shared review pipeline stays open to every
    reviewer — no grant should be created."""
    monkeypatch.setattr(ingest_router, "_ensure_table", AsyncMock(return_value="unchanged"))
    monkeypatch.setattr(ingest_router, "_next_record_id", AsyncMock(return_value="DEFAULT-2026-000001"))
    mock_db.execute.side_effect = [
        make_result(scalar="uploader-uuid-1"),  # SELECT created_by FROM batches
        make_result(scalar="doc-uuid-1"),       # INSERT ... RETURNING id
        make_result(),                          # log_action's audit INSERT
    ]
    asyncio.run(ingest_router._store_extraction(
        "batch-1", "/img.png", {"document_type": "arrete"}, "medium", "review_required",
        TENANT_ID, TENANT_SLUG,
    ))
    assert mock_db.execute.await_count == 3
    for call in mock_db.execute.call_args_list:
        assert "sdai_document_access" not in str(call[0][0])


def test_store_extraction_no_grant_when_no_uploader(mock_db, monkeypatch):
    """auto_approved but no created_by on the batch (predates this
    feature) — stays open, no grant, no crash."""
    monkeypatch.setattr(ingest_router, "_ensure_table", AsyncMock(return_value="unchanged"))
    monkeypatch.setattr(ingest_router, "_next_record_id", AsyncMock(return_value="DEFAULT-2026-000001"))
    mock_db.execute.side_effect = [
        make_result(scalar=None),               # SELECT created_by FROM batches — none
        make_result(scalar="doc-uuid-1"),       # INSERT ... RETURNING id
        make_result(),                          # log_action's audit INSERT
    ]
    asyncio.run(ingest_router._store_extraction(
        "batch-1", "/img.png", {"document_type": "arrete"}, "high", "auto_approved",
        TENANT_ID, TENANT_SLUG,
    ))
    assert mock_db.execute.await_count == 3
    for call in mock_db.execute.call_args_list:
        assert "sdai_document_access" not in str(call[0][0])


# ── _run_integrity_check ──────────────────────────────────────────────────────

def test_integrity_check_no_tables_returns_zero_counts(mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 0, "ok": 0, "mismatched": 0, "missing": 0}


def test_integrity_check_ok_when_files_present_and_hash_matches(mock_db, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"pdf-bytes")
    img = tmp_path / "doc.png"
    img.write_bytes(b"png-bytes")
    the_hash = hashlib.sha256(b"pdf-bytes").hexdigest()

    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_arrete",)]),                    # table discovery
        make_result(rows=[("doc-1", str(img), str(pdf), the_hash)]),  # per-table row select
    ]
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 1, "ok": 1, "mismatched": 0, "missing": 0}


def test_integrity_check_missing_image_file(mock_db, tmp_path):
    missing_img = tmp_path / "gone.png"
    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_arrete",)]),
        make_result(rows=[("doc-1", str(missing_img), None, None)]),
        make_result(),  # log_action's INSERT
    ]
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 1, "ok": 0, "mismatched": 0, "missing": 1}


def test_integrity_check_pdf_hash_mismatch(mock_db, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"actual-bytes")
    img = tmp_path / "doc.png"
    img.write_bytes(b"png-bytes")

    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_arrete",)]),
        make_result(rows=[("doc-1", str(img), str(pdf), "a-stale-hash-that-wont-match")]),
        make_result(),  # log_action's INSERT
    ]
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 1, "ok": 0, "mismatched": 1, "missing": 0}


def test_integrity_check_image_sourced_document_not_hash_checked(mock_db, tmp_path):
    """No source_pdf_path (image-sourced document) — only existence is
    checked. content_hash was computed against the raw upload, which
    _preprocess has since reoriented/cropped/re-encoded into
    source_image_path, so it would never match even when nothing is
    actually wrong — asserting only existence matters here."""
    img = tmp_path / "doc.png"
    img.write_bytes(b"preprocessed-png-bytes")
    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_arrete",)]),
        make_result(rows=[("doc-1", str(img), None, "hash-of-a-totally-different-raw-upload")]),
    ]
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 1, "ok": 1, "mismatched": 0, "missing": 0}


def test_integrity_check_missing_pdf_file(mock_db, tmp_path):
    img = tmp_path / "doc.png"
    img.write_bytes(b"png-bytes")
    missing_pdf = tmp_path / "gone.pdf"
    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_arrete",)]),
        make_result(rows=[("doc-1", str(img), str(missing_pdf), "some-hash")]),
        make_result(),  # log_action's INSERT
    ]
    result = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert result == {"checked": 1, "ok": 0, "mismatched": 0, "missing": 1}


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
    asyncio.run(ingest_router._register_and_enqueue("batch-dup", src, "dup.png", TENANT_ID, TENANT_SLUG))

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
    asyncio.run(ingest_router._register_and_enqueue("batch-new", src, "new.png", TENANT_ID, TENANT_SLUG))

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


# ── manual_enter_page: extraction-editing permission ──────────────────────────

def test_manual_enter_page_requires_extraction_editor(auth_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_resolve_tenant_for_page",
                        AsyncMock(return_value=(TENANT_ID, TENANT_SLUG)))
    resp = auth_client.post("/api/ingest/pages/page-1/manual", json={"fields": {"a": "b"}})
    assert resp.status_code == 403


def test_manual_enter_page_success_as_extraction_editor(extraction_editor_client, monkeypatch):
    monkeypatch.setattr(ingest_router, "_resolve_tenant_for_page",
                        AsyncMock(return_value=(TENANT_ID, TENANT_SLUG)))
    monkeypatch.setattr(ingest_router, "_manual_enter_page", AsyncMock())
    resp = extraction_editor_client.post("/api/ingest/pages/page-1/manual", json={"fields": {"a": "b"}})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_session(monkeypatch, *, fetchall):
    """Patch ingest_router.SessionLocal to return rows on fetchall().

    Also satisfies get_batch_status's tenant-ownership check (its first
    query, via .one_or_none()) by returning a row matching REVIEWER's
    tenant — both queries share the same mocked session.execute result."""
    from unittest.mock import MagicMock

    result = MagicMock()
    result.fetchall.return_value = fetchall
    result.one_or_none.return_value = (TENANT_ID,)

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit  = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__  = AsyncMock(return_value=False)

    monkeypatch.setattr(ingest_router, "SessionLocal",
                        MagicMock(return_value=session_ctx))
    return session
