"""Tests for /api/admin/* endpoints."""

import pytest
from conftest import make_result


# ── Auth & role guards ────────────────────────────────────────────────────────

def test_audit_log_requires_auth(client):
    assert client.get("/api/admin/audit-log").status_code == 401


def test_audit_log_requires_admin_role(auth_client):
    # auth_client is a reviewer — require_admin dependency should reject with 403
    assert auth_client.get("/api/admin/audit-log").status_code == 403


# ── Successful access (admin role) ────────────────────────────────────────────

def test_audit_log_returns_empty(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    resp = admin_client.get("/api/admin/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_audit_log_returns_items(admin_client, mock_db):
    rows = [
        {
            "id":          "log-uuid-1",
            "user_id":     "user-uuid-1",
            "user_email":  "admin@example.com",
            "action":      "document_approved",
            "table_name":  "my_table",
            "document_id": "doc-uuid-1",
            "details":     {"confidence": "high"},
            "ip_address":  "127.0.0.1",
            "created_at":  "2024-01-01T00:00:00",
            "total_count": 1,
        }
    ]
    mock_db.execute.return_value = make_result(rows=rows)

    resp = admin_client.get("/api/admin/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "document_approved"
    # total_count must be stripped from items
    assert "total_count" not in body["items"][0]


def test_audit_log_pagination_params(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    resp = admin_client.get("/api/admin/audit-log?page=2&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2
    assert body["page_size"] == 10


def test_audit_log_filter_by_action(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    resp = admin_client.get("/api/admin/audit-log?action=user_login")
    assert resp.status_code == 200


def test_audit_log_page_size_too_large_returns_422(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    resp = admin_client.get("/api/admin/audit-log?page_size=999")
    assert resp.status_code == 422


# ── Groups ────────────────────────────────────────────────────────────────────

def test_list_groups_requires_admin(auth_client):
    assert auth_client.get("/api/admin/groups").status_code == 403


def test_list_groups_returns_items(admin_client, mock_db):
    import datetime as _dt
    rows = [("group-1", "HR", _dt.datetime(2026, 1, 1), 3)]
    mock_db.execute.return_value = make_result(rows=rows)
    resp = admin_client.get("/api/admin/groups")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "HR"
    assert body[0]["member_count"] == 3


def test_create_group_success(admin_client, mock_db):
    mock_db.execute.return_value = make_result(scalar="group-uuid-1")
    resp = admin_client.post("/api/admin/groups", json={"name": "Management"})
    assert resp.status_code == 200
    assert resp.json() == {"id": "group-uuid-1", "name": "Management"}


def test_create_group_duplicate_returns_409(admin_client, mock_db):
    mock_db.execute.return_value = make_result(scalar=None)
    resp = admin_client.post("/api/admin/groups", json={"name": "HR"})
    assert resp.status_code == 409


def test_delete_group_not_found(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = admin_client.delete("/api/admin/groups/missing-id")
    assert resp.status_code == 404


def test_delete_group_success(admin_client, mock_db):
    mock_db.execute.side_effect = [
        make_result(one_or_none=("group-1",)),  # DELETE sdai_groups RETURNING id
        make_result(),                          # DELETE sdai_document_access grants
    ]
    resp = admin_client.delete("/api/admin/groups/group-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── Users ─────────────────────────────────────────────────────────────────────

def test_list_users_requires_admin(auth_client):
    assert auth_client.get("/api/admin/users").status_code == 403


def test_list_users_returns_items_with_groups(admin_client, mock_db):
    rows = [("user-1", "a@example.com", "Alice", "reviewer", False, ["group-1", "group-2"])]
    mock_db.execute.return_value = make_result(rows=rows)
    resp = admin_client.get("/api/admin/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["email"] == "a@example.com"
    assert body[0]["group_ids"] == ["group-1", "group-2"]


def test_update_user_can_manage_access(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=("user-1",))
    resp = admin_client.patch("/api/admin/users/user-1", json={"can_manage_access": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_update_user_not_found(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = admin_client.patch("/api/admin/users/missing-id", json={"can_manage_access": True})
    assert resp.status_code == 404


def test_add_user_to_group_success(admin_client, mock_db):
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # user exists
        make_result(one_or_none=(1,)),  # group exists
        make_result(),                  # INSERT membership
    ]
    resp = admin_client.post("/api/admin/users/user-1/groups", json={"group_id": "group-1"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_add_user_to_group_user_not_found(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = admin_client.post("/api/admin/users/missing/groups", json={"group_id": "group-1"})
    assert resp.status_code == 404


def test_remove_user_from_group(admin_client, mock_db):
    mock_db.execute.return_value = make_result()
    resp = admin_client.delete("/api/admin/users/user-1/groups/group-1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── Fixity / integrity check ─────────────────────────────────────────────────

def test_integrity_check_requires_admin(auth_client):
    assert auth_client.post("/api/admin/integrity-check").status_code == 403


def test_integrity_check_returns_summary(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])  # no tables at all
    resp = admin_client.post("/api/admin/integrity-check")
    assert resp.status_code == 200
    assert resp.json() == {"checked": 0, "ok": 0, "mismatched": 0, "missing": 0}
