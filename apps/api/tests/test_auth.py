"""Tests for /api/auth/* endpoints."""

import uuid
import pytest
from unittest.mock import AsyncMock

import api_server
from conftest import REVIEWER, make_result


# ── Login ─────────────────────────────────────────────────────────────────────

def _user_row(active: bool = True):
    return (
        str(uuid.uuid4()),  # id
        "admin@example.com",
        "Admin User",
        "admin",
        "$2b$12$hashed",    # hashed_password (never actually verified — monkeypatched)
        active,             # is_active
    )


def test_login_success(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server, "_pwd_ctx",
                        type("_FakePwdCtx", (), {"verify": staticmethod(lambda p, h: True)})())

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"
    assert "access_token" in resp.cookies


def test_login_sets_jwt_cookie(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server, "_pwd_ctx",
                        type("_FakePwdCtx", (), {"verify": staticmethod(lambda p, h: True)})())

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})

    cookie = resp.cookies.get("access_token")
    assert cookie is not None
    # TestClient may return cookie values with surrounding quotes (RFC 6265 quoting)
    assert "Bearer " in cookie


def test_login_wrong_password(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server, "_pwd_ctx",
                        type("_FakePwdCtx", (), {"verify": staticmethod(lambda p, h: False)})())

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})

    assert resp.status_code == 401


def test_login_unknown_email(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)

    resp = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "secret"})

    assert resp.status_code == 401


def test_login_inactive_user(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=_user_row(active=False))

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})

    assert resp.status_code == 401


def test_login_missing_password_field(client):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com"})
    assert resp.status_code == 422


def test_login_missing_email_field(client):
    resp = client.post("/api/auth/login", json={"password": "secret"})
    assert resp.status_code == 422


# ── /me ───────────────────────────────────────────────────────────────────────

def test_me_unauthenticated(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_authenticated(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200


def test_me_returns_correct_fields(auth_client):
    resp = auth_client.get("/api/auth/me")
    body = resp.json()
    assert body["email"] == REVIEWER.email
    assert body["role"]  == REVIEWER.role
    assert "id" in body
    assert "full_name" in body


def test_me_tampered_token_rejected(client):
    client.cookies.set("access_token", "Bearer this.is.not.a.valid.jwt")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# ── Logout ────────────────────────────────────────────────────────────────────

def test_logout_returns_ok(auth_client):
    resp = auth_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"message": "logged out"}


def test_logout_clears_cookie(auth_client):
    auth_client.post("/api/auth/logout")
    # After logout the protected endpoint must be inaccessible
    resp = auth_client.get("/api/auth/me")
    # The dep-override is still active so me() still returns 200 here,
    # but the cookie should be deleted from the jar.
    assert "access_token" not in auth_client.cookies
