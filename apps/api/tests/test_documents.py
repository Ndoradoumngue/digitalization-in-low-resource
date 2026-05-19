"""Tests for /api/db/* endpoints and _sanitize helper."""

import pytest
from unittest.mock import AsyncMock

import documents_router
from conftest import make_result


# ── _sanitize unit tests ──────────────────────────────────────────────────────

def test_sanitize_lowercases():
    assert documents_router._sanitize("OrderDeMission") == "orderdemission"


def test_sanitize_replaces_spaces_and_hyphens():
    assert documents_router._sanitize("ordre de mission") == "ordre_de_mission"
    assert documents_router._sanitize("ordre-de-mission") == "ordre_de_mission"


def test_sanitize_strips_special_chars():
    assert documents_router._sanitize("Type (2023)") == "type_2023"


def test_sanitize_strips_leading_digits():
    assert documents_router._sanitize("123abc") == "abc"


def test_sanitize_empty_string_returns_document():
    assert documents_router._sanitize("") == "document"


def test_sanitize_only_digits_returns_document():
    assert documents_router._sanitize("123") == "document"


def test_sanitize_truncates_to_63_chars():
    long_name = "a" * 100
    assert len(documents_router._sanitize(long_name)) == 63


# ── Auth guards ───────────────────────────────────────────────────────────────

def test_list_types_requires_auth(client):
    assert client.get("/api/db/types").status_code == 401


def test_list_documents_requires_auth(client):
    assert client.get("/api/db/documents").status_code == 401


def test_get_document_detail_requires_auth(client):
    assert client.get("/api/db/documents/my_table/some-id").status_code == 401


def test_get_schema_requires_auth(client):
    assert client.get("/api/db/schema").status_code == 401


def test_serve_image_requires_auth(client):
    assert client.get("/api/db/image?path=/data/images/scan.png").status_code == 401


# ── list_types ────────────────────────────────────────────────────────────────

def test_list_types_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/types")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_types_returns_table_info(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"ordre_de_mission": {"id", "source_image_path"}}))
    mock_db.execute.return_value = make_result(one=(5, None, "ordre_de_mission"))

    resp = auth_client.get("/api/db/types")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["table_name"] == "ordre_de_mission"
    assert items[0]["count"] == 5


# ── list_documents ────────────────────────────────────────────────────────────

def test_list_documents_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["results"] == []


def test_list_documents_returns_results(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={
                            "my_table": {
                                "id", "source_image_path", "ingested_at",
                                "confidence", "review_status",
                            }
                        }))
    doc = {
        "id": "doc-uuid-1",
        "table_name": "my_table",
        "document_type": None,
        "reference_number": None,
        "date": None,
        "organisation": None,
        "destination_or_subject": None,
        "signatory": None,
        "confidence": "high",
        "review_status": "auto_approved",
        "ingested_at": "2024-01-01T00:00:00",
        "source_image_path": "/data/images/scan.png",
        "total_count": 1,
    }
    mock_db.execute.return_value = make_result(rows=[doc])

    resp = auth_client.get("/api/db/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["id"] == "doc-uuid-1"


def test_list_documents_unknown_type_returns_empty(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"known_table": {"id"}}))
    resp = auth_client.get("/api/db/documents?document_type=nonexistent")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── get_document_detail ───────────────────────────────────────────────────────

def test_get_document_detail_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/documents/unknown_table/some-id")
    assert resp.status_code == 404


def test_get_document_detail_doc_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id", "source_image_path"}}))
    mock_db.execute.return_value = make_result(one_or_none=None)

    resp = auth_client.get("/api/db/documents/my_table/nonexistent-id")
    assert resp.status_code == 404


def test_get_document_detail_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id", "reference_number"}}))
    row = {"id": "doc-uuid-1", "reference_number": "REF-001"}
    mock_db.execute.return_value = make_result(one_or_none=row)

    resp = auth_client.get("/api/db/documents/my_table/doc-uuid-1")
    assert resp.status_code == 200
    assert resp.json()["reference_number"] == "REF-001"


# ── get_schema ────────────────────────────────────────────────────────────────

def test_get_schema_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/schema")
    assert resp.status_code == 200
    assert resp.json() == {"tables": []}


def test_get_schema_returns_table_metadata(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id", "source_image_path"}}))

    col_rows = [("my_table", "id", "uuid", "NO"), ("my_table", "source_image_path", "text", "YES")]
    fk_rows  = []
    stat_one = (3, None, "my_document_type")

    mock_db.execute.side_effect = [
        make_result(rows=col_rows),
        make_result(rows=fk_rows),
        make_result(one=stat_one),
        make_result(),  # spare for any extra calls
    ]

    resp = auth_client.get("/api/db/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["tables"]) == 1
    assert body["tables"][0]["name"] == "my_table"
    assert body["tables"][0]["row_count"] == 3


# ── serve_processed_image: path traversal protection ─────────────────────────

def test_serve_image_path_traversal_denied(auth_client, tmp_path, monkeypatch):
    import documents_router as dr
    monkeypatch.setattr(dr, "DATA_DIR", tmp_path)

    resp = auth_client.get("/api/db/image?path=../../etc/passwd")
    assert resp.status_code == 403


def test_serve_image_not_found(auth_client, tmp_path, monkeypatch):
    import documents_router as dr
    monkeypatch.setattr(dr, "DATA_DIR", tmp_path)
    (tmp_path / "images").mkdir()

    resp = auth_client.get(f"/api/db/image?path={tmp_path}/images/missing.png")
    assert resp.status_code == 404
