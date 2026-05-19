"""Tests for /api/review/* endpoints."""

import pytest
from unittest.mock import AsyncMock

import review_router
from conftest import make_result

# Columns that include audit fields so the DDL branches are skipped
_FULL_COLS = frozenset({
    "id", "source_image_path", "ingested_at", "confidence", "review_status",
    "document_type", "reference_number", "date", "organisation",
    "destination_or_subject", "signatory", "reviewed_at", "reviewed_by",
})


def _fake_tables(cols=_FULL_COLS):
    return {"my_table": set(cols)}


# ── Auth guards ───────────────────────────────────────────────────────────────

def test_review_count_requires_auth(client):
    assert client.get("/api/review/count").status_code == 401


def test_review_queue_requires_auth(client):
    assert client.get("/api/review/queue").status_code == 401


def test_patch_review_requires_auth(client):
    assert client.patch("/api/review/my_table/doc-id",
                        json={"action": "approve", "fields": {}}).status_code == 401


def test_flag_review_requires_auth(client):
    assert client.post("/api/review/my_table/doc-id/flag").status_code == 401


# ── review_count ──────────────────────────────────────────────────────────────

def test_review_count_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/review/count")
    assert resp.status_code == 200
    assert resp.json()["pending"] == 0


def test_review_count_returns_value(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id"}}))
    mock_db.execute.return_value = make_result(scalar=7)

    resp = auth_client.get("/api/review/count")
    assert resp.status_code == 200
    assert resp.json()["pending"] == 7


# ── review_queue ──────────────────────────────────────────────────────────────

def test_review_queue_empty(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/review/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_review_queue_returns_items(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": _FULL_COLS}))
    row = {
        "id": "doc-1",
        "table_name": "my_table",
        "document_type": "ordre_de_mission",
        "reference_number": None,
        "date": None,
        "organisation": None,
        "destination_or_subject": None,
        "signatory": None,
        "confidence": "medium",
        "review_status": "review_required",
        "ingested_at": "2024-01-01T00:00:00",
        "source_image_path": "/data/img.png",
        "total_count": 1,
    }
    mock_db.execute.return_value = make_result(rows=[row])

    resp = auth_client.get("/api/review/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "doc-1"


def test_review_queue_unknown_type_returns_empty(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={"known": {"id"}}))
    resp = auth_client.get("/api/review/queue?document_type=unknown_type")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── patch_review ──────────────────────────────────────────────────────────────

def test_patch_review_invalid_action(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))
    resp = auth_client.patch("/api/review/my_table/doc-id",
                             json={"action": "delete", "fields": {}})
    assert resp.status_code == 422


def test_patch_review_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.patch("/api/review/my_table/doc-id",
                             json={"action": "approve", "fields": {}})
    assert resp.status_code == 404


def test_patch_review_doc_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))

    type_rows = [("id", "uuid"), ("confidence", "text"), ("review_status", "text")]
    mock_db.execute.side_effect = [
        make_result(rows=type_rows),           # col types query
        make_result(one_or_none=None),          # UPDATE ... RETURNING * → no row
        make_result(),                          # log_action audit INSERT
    ]

    resp = auth_client.patch("/api/review/my_table/missing-id",
                             json={"action": "approve", "fields": {}})
    assert resp.status_code == 404


def test_patch_review_approve(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))

    updated_row = {
        "id": "doc-1",
        "review_status": "approved",
        "confidence": "medium",
        "reviewed_at": None,
        "reviewed_by": None,
    }
    type_rows = [("id", "uuid"), ("confidence", "text"), ("review_status", "text")]
    mock_db.execute.side_effect = [
        make_result(rows=type_rows),            # col types
        make_result(one_or_none=updated_row),   # UPDATE RETURNING *
        make_result(),                          # log_action
    ]

    resp = auth_client.patch(
        "/api/review/my_table/doc-1",
        json={"action": "approve", "fields": {"reference_number": "REF-001"}},
    )

    assert resp.status_code == 200
    assert resp.json()["review_status"] == "approved"


def test_patch_review_reject(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))

    updated_row = {"id": "doc-2", "review_status": "rejected", "confidence": "low"}
    mock_db.execute.side_effect = [
        make_result(one_or_none=updated_row),   # UPDATE ... RETURNING *
        make_result(),                          # log_action
    ]

    resp = auth_client.patch("/api/review/my_table/doc-2",
                             json={"action": "reject", "fields": {}})

    assert resp.status_code == 200
    assert resp.json()["review_status"] == "rejected"


# ── flag_review ───────────────────────────────────────────────────────────────

def test_flag_review_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.post("/api/review/my_table/doc-id/flag")
    assert resp.status_code == 404


def test_flag_review_doc_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))
    mock_db.execute.return_value = make_result(rowcount=0)

    resp = auth_client.post("/api/review/my_table/missing-id/flag")
    assert resp.status_code == 404


def test_flag_review_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns",
                        AsyncMock(return_value=_fake_tables()))
    mock_db.execute.side_effect = [
        make_result(rowcount=1),   # UPDATE ... RETURNING id
        make_result(),             # log_action
    ]

    resp = auth_client.post("/api/review/my_table/doc-1/flag")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
