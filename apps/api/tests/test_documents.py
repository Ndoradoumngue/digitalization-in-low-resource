"""Tests for /api/db/* endpoints and _sanitize helper."""

import pytest
from unittest.mock import AsyncMock

import documents_router
from conftest import ADMIN, REVIEWER, make_result


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


# ── _searchable_cols / _extra_cols unit tests ─────────────────────────────────
# Generalized-search classification: any TEXT column not in _BASE_COLS is
# searchable/extra; document_type is searchable but never in extra_fields
# (it gets its own dedicated projected column instead).

def test_searchable_cols_excludes_base_and_non_text():
    cols = {
        "id": "uuid", "source_image_path": "text", "confidence": "text",
        "reference_number": "text", "person_names": "jsonb",
    }
    assert documents_router._searchable_cols(cols) == ["reference_number"]


def test_searchable_cols_includes_document_type():
    cols = {"id": "uuid", "document_type": "text", "reference_number": "text"}
    assert set(documents_router._searchable_cols(cols)) == {"document_type", "reference_number"}


def test_searchable_cols_empty_when_no_text_fields():
    cols = {"id": "uuid", "source_image_path": "text", "person_names": "jsonb"}
    assert documents_router._searchable_cols(cols) == []


def test_extra_cols_excludes_base_and_document_type():
    cols = {
        "id": "uuid", "source_image_path": "text", "confidence": "text",
        "document_type": "text", "reference_number": "text", "person_names": "jsonb",
    }
    assert set(documents_router._extra_cols(cols)) == {"reference_number", "person_names"}


# ── _per_table_select generalized-search SQL ──────────────────────────────────

def test_per_table_select_builds_extra_fields_from_actual_columns():
    """A non-admin-document schema (e.g. a lexicon table with no
    reference_number/organisation/etc.) must still be projectable and, if
    it has any TEXT field, searchable — this was the concrete gap the
    hardcoded field list left before generalized search."""
    cols = {"id": "uuid", "source_image_path": "text", "confidence": "text",
            "review_status": "text", "ingested_at": "timestamp with time zone",
            "kabalay": "text", "french": "text", "entries": "jsonb"}
    sql = documents_router._per_table_select(
        "t_default_lexique", cols,
        q="mot", confidence=None, review_status=None, date_from=None, date_to=None,
        current_user=REVIEWER,
    )
    assert sql is not None
    assert "jsonb_build_object" in sql
    assert "'kabalay', kabalay" in sql
    assert "'french', french" in sql
    assert "'entries', entries" in sql
    assert "COALESCE(kabalay,'')" in sql and "COALESCE(french,'')" in sql


def test_per_table_select_excludes_table_with_no_text_cols_when_searching():
    cols = {"id": "uuid", "source_image_path": "text", "entries": "jsonb"}
    sql = documents_router._per_table_select(
        "t_default_all_jsonb", cols,
        q="mot", confidence=None, review_status=None, date_from=None, date_to=None,
        current_user=REVIEWER,
    )
    assert sql is None


def test_per_table_select_reviewed_by_filter():
    cols = {"id": "uuid", "source_image_path": "text", "reviewed_by": "uuid"}
    sql = documents_router._per_table_select(
        "t_default_arrete", cols,
        q=None, confidence=None, review_status=None, date_from=None, date_to=None,
        reviewed_by="some-user-id", current_user=REVIEWER,
    )
    assert sql is not None
    assert "reviewed_by = CAST(:reviewed_by AS uuid)" in sql


def test_per_table_select_excludes_table_without_reviewed_by_column():
    """A table that has never had a document reviewed (no reviewed_by
    column yet, per patch_review's lazy ALTER) can't match a reviewed_by
    filter — it must be excluded from the UNION, not error."""
    cols = {"id": "uuid", "source_image_path": "text"}
    sql = documents_router._per_table_select(
        "t_default_never_reviewed", cols,
        q=None, confidence=None, review_status=None, date_from=None, date_to=None,
        reviewed_by="some-user-id", current_user=REVIEWER,
    )
    assert sql is None


def test_per_table_select_series_filter():
    cols = {"id": "uuid", "source_image_path": "text", "series_id": "uuid"}
    sql = documents_router._per_table_select(
        "t_default_arrete", cols,
        q=None, confidence=None, review_status=None, date_from=None, date_to=None,
        series="some-series-id", current_user=REVIEWER,
    )
    assert sql is not None
    assert "series_id = CAST(:series AS uuid)" in sql
    assert "series_id::text AS series_id" in sql


# ── _access_where_clause / document-level access control ─────────────────────

def test_access_where_clause_empty_for_admin():
    """Admins get no filter at all — see everything, no matter what
    sdai_document_access says."""
    assert documents_router._access_where_clause("t_default_arrete", ADMIN) == ""


def test_access_where_clause_present_for_reviewer():
    clause = documents_router._access_where_clause("t_default_arrete", REVIEWER)
    assert clause != ""
    assert "sdai_document_access" in clause
    assert "t_default_arrete" in clause


def test_per_table_select_includes_access_clause_for_non_admin():
    cols = {"id": "uuid", "source_image_path": "text", "reference_number": "text"}
    sql = documents_router._per_table_select(
        "t_default_arrete", cols,
        q=None, confidence=None, review_status=None, date_from=None, date_to=None,
        current_user=REVIEWER,
    )
    assert "sdai_document_access" in sql


def test_per_table_select_omits_access_clause_for_admin():
    cols = {"id": "uuid", "source_image_path": "text"}
    sql = documents_router._per_table_select(
        "t_default_arrete", cols,
        q=None, confidence=None, review_status=None, date_from=None, date_to=None,
        current_user=ADMIN,
    )
    assert "sdai_document_access" not in sql


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
                        AsyncMock(return_value={"ordre_de_mission": {"id": "uuid", "source_image_path": "text"}}))
    mock_db.execute.return_value = make_result(one=(5, None, "ordre_de_mission"))

    resp = auth_client.get("/api/db/types")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["table_name"] == "ordre_de_mission"
    assert items[0]["count"] == 5


# ── list_reviewers ────────────────────────────────────────────────────────────

def test_list_reviewers_empty_when_no_reviewed_tables(auth_client, mock_db, monkeypatch):
    """A table that has never had a document approved/rejected has no
    reviewed_by column yet (patch_review adds it lazily) — it must not be
    queried for reviewer ids at all."""
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    resp = auth_client.get("/api/db/reviewers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_reviewers_returns_resolved_names(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={
                            "my_table": {"id": "uuid", "reviewed_by": "uuid"},
                        }))
    reviewer_id = "11111111-1111-1111-1111-111111111111"
    mock_db.execute.side_effect = [
        make_result(rows=[(reviewer_id,)]),                          # id union
        make_result(rows=[(reviewer_id, "r@example.com", "Rita")]),  # user resolve
    ]
    resp = auth_client.get("/api/db/reviewers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == reviewer_id
    assert body[0]["email"] == "r@example.com"
    assert body[0]["full_name"] == "Rita"


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
                                "id": "uuid", "source_image_path": "text",
                                "ingested_at": "timestamp with time zone",
                                "confidence": "text", "review_status": "text",
                            }
                        }))
    doc = {
        "id": "doc-uuid-1",
        "table_name": "my_table",
        "document_type": None,
        "confidence": "high",
        "review_status": "auto_approved",
        "ingested_at": "2024-01-01T00:00:00",
        "source_image_path": "/data/images/scan.png",
        "extra_fields": {},
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
                        AsyncMock(return_value={"known_table": {"id": "uuid"}}))
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
                        AsyncMock(return_value={"my_table": {"id": "uuid", "source_image_path": "text"}}))
    mock_db.execute.return_value = make_result(one_or_none=None)

    resp = auth_client.get("/api/db/documents/my_table/nonexistent-id")
    assert resp.status_code == 404


def test_get_document_detail_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid", "reference_number": "text"}}))
    row = {"id": "doc-uuid-1", "reference_number": "REF-001"}
    mock_db.execute.return_value = make_result(one_or_none=row)

    resp = auth_client.get("/api/db/documents/my_table/doc-uuid-1")
    assert resp.status_code == 200
    assert resp.json()["reference_number"] == "REF-001"


# ── document links ────────────────────────────────────────────────────────────

def test_get_document_links_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/documents/my_table/doc-1/links")
    assert resp.status_code == 404


def test_get_document_links_returns_enriched(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={
                            "my_table":    {"id": "uuid", "source_image_path": "text"},
                            "other_table": {"id": "uuid", "reference_number": "text"},
                        }))
    link_row = {
        "id": "link-1", "from_table": "my_table", "from_id": "doc-1",
        "to_table": "other_table", "to_id": "doc-2", "relation": "concerns",
        "note": None, "created_by": None, "created_at": None,
    }
    other_row = {
        "id": "doc-2", "document_type": "titre_foncier",
        "source_image_path": "/img.png", "display": "REF-99",
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),    # _document_visible(my_table, doc-1)
        make_result(rows=[link_row]),
        make_result(rows=[other_row]),
    ]

    resp = auth_client.get("/api/db/documents/my_table/doc-1/links")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["links"]) == 1
    assert body["links"][0]["direction"] == "outgoing"
    assert body["links"][0]["other_table"] == "other_table"
    assert body["links"][0]["other_display"] == "REF-99"
    assert body["links"][0]["other_missing"] is False


def test_create_document_link_self_link_rejected(auth_client, mock_db, monkeypatch):
    resp = auth_client.post(
        "/api/db/documents/my_table/doc-1/links",
        json={"to_table": "my_table", "to_id": "doc-1", "relation": "concerns"},
    )
    assert resp.status_code == 400


def test_create_document_link_unknown_target_table_rejected(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    resp = auth_client.post(
        "/api/db/documents/my_table/doc-1/links",
        json={"to_table": "other_tenants_table", "to_id": "doc-2", "relation": "concerns"},
    )
    assert resp.status_code == 404


def test_create_document_link_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={
                            "my_table":    {"id": "uuid"},
                            "other_table": {"id": "uuid"},
                        }))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),      # from doc exists
        make_result(one_or_none=(1,)),      # to doc exists
        make_result(scalar="link-uuid-1"),  # INSERT ... RETURNING id
        make_result(),                      # log_action
    ]

    resp = auth_client.post(
        "/api/db/documents/my_table/doc-1/links",
        json={"to_table": "other_table", "to_id": "doc-2", "relation": "concerns"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "link-uuid-1"}


def test_create_document_link_duplicate_returns_existing(auth_client, mock_db, monkeypatch):
    """Concurrent/duplicate identical link requests converge to the same
    row (ON CONFLICT DO NOTHING + fallback SELECT) instead of erroring."""
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={
                            "my_table":    {"id": "uuid"},
                            "other_table": {"id": "uuid"},
                        }))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),           # from doc exists
        make_result(one_or_none=(1,)),           # to doc exists
        make_result(scalar=None),                # INSERT ... ON CONFLICT DO NOTHING -> no row
        make_result(scalar="existing-link-id"),  # fallback SELECT
        make_result(),                           # log_action
    ]

    resp = auth_client.post(
        "/api/db/documents/my_table/doc-1/links",
        json={"to_table": "other_table", "to_id": "doc-2", "relation": "concerns"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "existing-link-id"}


def test_delete_document_link_not_found(auth_client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = auth_client.delete("/api/db/links/missing-id")
    assert resp.status_code == 404


def test_delete_document_link_success(auth_client, mock_db, monkeypatch):
    link_row = {
        "from_table": "my_table", "from_id": "doc-1",
        "to_table": "other_table", "to_id": "doc-2", "relation": "concerns",
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=link_row),   # SELECT the link
        make_result(one_or_none=(1,)),       # _document_visible(from) — both checks always run (no short-circuit)
        make_result(one_or_none=(1,)),       # _document_visible(to)
        make_result(one_or_none=link_row),   # DELETE ... RETURNING
        make_result(),                       # log_action
    ]
    resp = auth_client.delete("/api/db/links/link-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── document access grants ────────────────────────────────────────────────────

def test_get_document_access_requires_visibility(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.return_value = make_result(one_or_none=None)  # not visible
    resp = auth_client.get("/api/db/documents/my_table/doc-1/access")
    assert resp.status_code == 404


def test_get_document_access_returns_grants(auth_client, mock_db, monkeypatch):
    import datetime as _dt
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    grant_row = {
        "id": "grant-1", "grantee_type": "group", "grantee_id": "group-1",
        "granted_by": None, "granted_at": _dt.datetime(2026, 1, 1),
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),                     # _document_visible
        make_result(rows=[grant_row]),                     # SELECT grants
        make_result(rows=[("group-1", "HR")]),              # resolve group names
    ]
    resp = auth_client.get("/api/db/documents/my_table/doc-1/access")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["grants"]) == 1
    assert body["grants"][0]["grantee_name"] == "HR"
    assert body["can_manage"] is False  # plain reviewer


def test_create_document_access_requires_access_manager(auth_client, mock_db, monkeypatch):
    """A plain reviewer (not admin, not can_manage_access) is rejected —
    tagging a document with an access grant is the delegated permission."""
    resp = auth_client.post(
        "/api/db/documents/my_table/doc-1/access",
        json={"grantee_type": "group", "grantee_id": "group-1"},
    )
    assert resp.status_code == 403


def test_create_document_access_success_as_access_manager(access_manager_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),          # _document_visible
        make_result(one_or_none=(1,)),          # grantee (group) exists
        make_result(scalar="grant-uuid-1"),     # INSERT ... RETURNING id
        make_result(),                          # log_action
    ]
    resp = access_manager_client.post(
        "/api/db/documents/my_table/doc-1/access",
        json={"grantee_type": "group", "grantee_id": "group-1"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "grant-uuid-1"}


def test_delete_document_access_requires_access_manager(auth_client, mock_db):
    resp = auth_client.delete("/api/db/access/grant-1")
    assert resp.status_code == 403


def test_delete_document_access_not_found(access_manager_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = access_manager_client.delete("/api/db/access/missing-id")
    assert resp.status_code == 404


def test_delete_document_access_success(access_manager_client, mock_db):
    deleted_row = {
        "table_name": "my_table", "document_id": "doc-1",
        "grantee_type": "group", "grantee_id": "group-1",
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=deleted_row),  # DELETE ... RETURNING
        make_result(),                         # log_action
    ]
    resp = access_manager_client.delete("/api/db/access/grant-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── fonds/series hierarchy ─────────────────────────────────────────────────────

def test_list_series_returns_items(auth_client, mock_db):
    import datetime as _dt
    rows = [("series-1", "Land Deeds", "1990-2000", _dt.datetime(2026, 1, 1))]
    mock_db.execute.return_value = make_result(rows=rows)
    resp = auth_client.get("/api/db/series")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Land Deeds"
    assert body[0]["description"] == "1990-2000"


def test_create_series_requires_admin(auth_client):
    resp = auth_client.post("/api/db/series", json={"name": "Land Deeds"})
    assert resp.status_code == 403


def test_create_series_success(admin_client, mock_db):
    mock_db.execute.side_effect = [
        make_result(scalar="series-uuid-1"),  # INSERT ... RETURNING id
        make_result(),                        # log_action
    ]
    resp = admin_client.post("/api/db/series", json={"name": "Land Deeds"})
    assert resp.status_code == 200
    assert resp.json() == {"id": "series-uuid-1", "name": "Land Deeds"}


def test_create_series_duplicate_returns_409(admin_client, mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    resp = admin_client.post("/api/db/series", json={"name": "Land Deeds"})
    assert resp.status_code == 409


def test_delete_series_requires_admin(auth_client):
    resp = auth_client.delete("/api/db/series/series-1")
    assert resp.status_code == 403


def test_delete_series_not_found(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = admin_client.delete("/api/db/series/missing-id")
    assert resp.status_code == 404


def test_delete_series_success(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=("series-1",))
    resp = admin_client.delete("/api/db/series/series-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_assign_document_series_table_not_found(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.patch(
        "/api/db/documents/missing_table/doc-1/series", json={"series_id": "series-1"}
    )
    assert resp.status_code == 404


def test_assign_document_series_requires_visibility(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.return_value = make_result(one_or_none=None)  # not visible
    resp = auth_client.patch(
        "/api/db/documents/my_table/doc-1/series", json={"series_id": "series-1"}
    )
    assert resp.status_code == 404


def test_assign_document_series_unknown_series_rejected(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),    # _document_visible
        make_result(one_or_none=None),    # series lookup — not found
    ]
    resp = auth_client.patch(
        "/api/db/documents/my_table/doc-1/series", json={"series_id": "missing-series"}
    )
    assert resp.status_code == 404


def test_assign_document_series_success(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible
        make_result(one_or_none=(1,)),  # series lookup — found
        make_result(),                  # UPDATE
        make_result(),                  # log_action
    ]
    resp = auth_client.patch(
        "/api/db/documents/my_table/doc-1/series", json={"series_id": "series-1"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_unassign_document_series_success(auth_client, mock_db, monkeypatch):
    """series_id: null clears the assignment — no series lookup needed."""
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid"}}))
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # _document_visible
        make_result(),                  # UPDATE
        make_result(),                  # log_action
    ]
    resp = auth_client.patch(
        "/api/db/documents/my_table/doc-1/series", json={"series_id": None}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── get_schema ────────────────────────────────────────────────────────────────

def test_get_schema_empty_db(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={}))
    resp = auth_client.get("/api/db/schema")
    assert resp.status_code == 200
    assert resp.json() == {"tables": []}


def test_get_schema_returns_table_metadata(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns",
                        AsyncMock(return_value={"my_table": {"id": "uuid", "source_image_path": "text"}}))

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
