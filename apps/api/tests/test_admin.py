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
