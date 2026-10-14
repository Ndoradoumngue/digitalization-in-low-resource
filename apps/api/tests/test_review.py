"""Tests for /api/review/* endpoints."""

from unittest.mock import AsyncMock

import review_router
from conftest import make_result

# Columns that include audit fields so the DDL branches are skipped.
# {col_name: data_type} - matches _get_tables_columns's return shape.
_FULL_COLS = {
    "id": "uuid",
    "source_image_path": "text",
    "ingested_at": "timestamp with time zone",
    "confidence": "text",
    "review_status": "text",
    "document_type": "text",
    "reference_number": "text",
    "date": "text",
    "organisation": "text",
    "destination_or_subject": "text",
    "signatory": "text",
    "reviewed_at": "timestamp with time zone",
    "reviewed_by": "uuid",
}


def _fake_tables(cols=_FULL_COLS):
    return {"my_table": dict(cols)}


# ── Auth guards ───────────────────────────────────────────────────────────────


def test_review_count_requires_auth(client):
    assert client.get("/api/review/count").status_code == 401


def test_review_queue_requires_auth(client):
    assert client.get("/api/review/queue").status_code == 401


def test_patch_review_requires_auth(client):
    assert (
        client.patch(
            "/api/review/my_table/doc-id", json={"action": "approve", "fields": {}}
        ).status_code
        == 401
    )


def test_flag_review_requires_auth(client):
    assert client.post("/api/review/my_table/doc-id/flag").status_code == 401


# ── review_count ──────────────────────────────────────────────────────────────


def test_review_count_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns", AsyncMock(return_value={}))
    resp = auth_client.get("/api/review/count")
    assert resp.status_code == 200
    assert resp.json()["pending"] == 0


def test_review_count_returns_value(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value={"my_table": {"id": "uuid"}})
    )
    mock_db.execute.return_value = make_result(scalar=7)

    resp = auth_client.get("/api/review/count")
    assert resp.status_code == 200
    assert resp.json()["pending"] == 7


# ── review_queue ──────────────────────────────────────────────────────────────


def test_review_queue_empty(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns", AsyncMock(return_value={}))
    resp = auth_client.get("/api/review/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_review_queue_returns_items(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value={"my_table": _FULL_COLS})
    )
    row = {
        "id": "doc-1",
        "table_name": "my_table",
        "document_type": "ordre_de_mission",
        "confidence": "medium",
        "review_status": "review_required",
        "ingested_at": "2024-01-01T00:00:00",
        "source_image_path": "/data/img.png",
        "extra_fields": {"reference_number": None, "organisation": None},
        "total_count": 1,
    }
    mock_db.execute.return_value = make_result(rows=[row])

    resp = auth_client.get("/api/review/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "doc-1"


def test_review_queue_unknown_type_returns_empty(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value={"known": {"id": "uuid"}})
    )
    resp = auth_client.get("/api/review/queue?document_type=unknown_type")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── patch_review ──────────────────────────────────────────────────────────────


def test_patch_review_invalid_action(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    resp = auth_client.patch("/api/review/my_table/doc-id", json={"action": "delete", "fields": {}})
    assert resp.status_code == 422


def test_patch_review_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns", AsyncMock(return_value={}))
    resp = auth_client.patch(
        "/api/review/my_table/doc-id", json={"action": "approve", "fields": {}}
    )
    assert resp.status_code == 404


def test_patch_review_doc_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )

    # patch_review no longer issues a separate column-types query - cols
    # (from _get_tables_columns) already carries data_type - so only the
    # UPDATE query executes; log_action is never reached on a 404.
    mock_db.execute.side_effect = [
        make_result(one_or_none=None),  # edit-level visibility check → no row
        make_result(one_or_none=None),  # read-level check → not visible at all → 404
    ]

    resp = auth_client.patch(
        "/api/review/my_table/missing-id", json={"action": "approve", "fields": {}}
    )
    assert resp.status_code == 404


def test_patch_review_approve(extraction_editor_client, mock_db, monkeypatch):
    """Approving WITH field corrections needs the extraction-editing
    permission - see test_patch_review_approve_field_edit_requires_permission
    for the 403 case."""
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )

    updated_row = {
        "id": "doc-1",
        "review_status": "approved",
        "confidence": "medium",
        "reviewed_at": None,
        "reviewed_by": None,
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible check
        make_result(one_or_none={"id": "doc-1", "reference_number": None}),  # old row
        make_result(one_or_none={**updated_row, "reference_number": "REF-001"}),  # UPDATE RETURNING *
        make_result(),  # INSERT sdai_field_history
        make_result(),  # log_action
    ]

    resp = extraction_editor_client.patch(
        "/api/review/my_table/doc-1",
        json={"action": "approve", "fields": {"reference_number": "REF-001"}},
    )

    assert resp.status_code == 200
    assert resp.json()["review_status"] == "approved"
    history = mock_db.execute.call_args_list[3].args
    assert "INSERT INTO sdai_field_history" in str(history[0])
    assert history[1]["source"] == "review" and history[1]["new"] == '"REF-001"'


def test_patch_review_approve_field_edit_requires_permission(auth_client, mock_db, monkeypatch):
    """A plain reviewer (no can_edit_extraction) can still approve a
    document, but not with field corrections attached."""
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    mock_db.execute.return_value = make_result(one_or_none=(1,))  # _document_visible check

    resp = auth_client.patch(
        "/api/review/my_table/doc-1",
        json={"action": "approve", "fields": {"reference_number": "REF-001"}},
    )
    assert resp.status_code == 403


def test_patch_review_approve_grants_uploader_access(auth_client, mock_db, monkeypatch):
    """Approving a document is exactly the moment it leaves the shared
    review pipeline - if it has a known uploader, that should create an
    access grant so privacy applies from here on (see
    ingest_router._grant_uploader_access)."""
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )

    updated_row = {
        "id": "doc-1",
        "review_status": "approved",
        "confidence": "medium",
        "reviewed_at": None,
        "reviewed_by": None,
        "uploaded_by": "uploader-uuid-1",
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible check
        make_result(one_or_none=updated_row),  # UPDATE RETURNING *
        make_result(),  # INSERT sdai_document_access (grant)
        make_result(),  # log_action
    ]

    resp = auth_client.patch(
        "/api/review/my_table/doc-1",
        json={"action": "approve", "fields": {}},
    )

    assert resp.status_code == 200
    grant_sql = str(mock_db.execute.call_args_list[2][0][0])
    assert "INSERT INTO sdai_document_access" in grant_sql


def test_patch_review_reject(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )

    updated_row = {"id": "doc-2", "review_status": "rejected", "confidence": "low"}
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible check
        make_result(one_or_none=updated_row),  # UPDATE ... RETURNING *
        make_result(),  # log_action
    ]

    resp = auth_client.patch("/api/review/my_table/doc-2", json={"action": "reject", "fields": {}})

    assert resp.status_code == 200
    assert resp.json()["review_status"] == "rejected"


# ── flag_review ───────────────────────────────────────────────────────────────


def test_flag_review_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(review_router, "_get_tables_columns", AsyncMock(return_value={}))
    resp = auth_client.post("/api/review/my_table/doc-id/flag")
    assert resp.status_code == 404


def test_flag_review_doc_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    mock_db.execute.return_value = make_result(rowcount=0)

    resp = auth_client.post("/api/review/my_table/missing-id/flag")
    assert resp.status_code == 404


def test_flag_review_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible check
        make_result(rowcount=1),  # UPDATE ... RETURNING id
        make_result(),  # log_action
    ]

    resp = auth_client.post("/api/review/my_table/doc-1/flag")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── delete_document ───────────────────────────────────────────────────────────


def test_delete_document_requires_lifecycle_manager(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    resp = auth_client.delete("/api/review/my_table/doc-1")
    assert resp.status_code == 403


def test_delete_document_unknown_table(admin_client, mock_db, monkeypatch):
    import lifecycle_router

    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    monkeypatch.setattr(lifecycle_router, "trash_document", AsyncMock())
    resp = admin_client.delete("/api/review/other_table/doc-1")
    assert resp.status_code == 404
    lifecycle_router.trash_document.assert_not_awaited()


def test_delete_document_moves_to_trash(admin_client, mock_db, monkeypatch):
    """GED Phase 6: the old hard delete is a trash - nothing is deleted
    and links stay (hidden with the document)."""
    import lifecycle_router

    monkeypatch.setattr(
        review_router, "_get_tables_columns", AsyncMock(return_value=_fake_tables())
    )
    monkeypatch.setattr(lifecycle_router, "trash_document", AsyncMock())
    resp = admin_client.delete("/api/review/my_table/doc-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    args = lifecycle_router.trash_document.await_args
    assert args.args[1:3] == ("my_table", "doc-1")
    assert args.kwargs["via"] == "review"
