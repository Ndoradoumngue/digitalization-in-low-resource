"""Tests for /api/auth/* endpoints."""

import uuid

import api_server
from conftest import REVIEWER, TENANT_ID, TENANT_NAME, TENANT_SLUG, make_result

# ── Login ─────────────────────────────────────────────────────────────────────


def _user_row(active: bool = True, tenant_active: bool = True):
    return (
        str(uuid.uuid4()),  # id
        "admin@example.com",
        "Admin User",
        "admin",
        "$2b$12$hashed",  # hashed_password (never actually verified - monkeypatched)
        active,  # is_active
        TENANT_SLUG,  # tenant_slug
        TENANT_NAME,  # tenant_name
        tenant_active,  # tenant_is_active
        False,  # can_manage_access
        False,  # can_edit_extraction
    )


def test_login_success(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server._bcrypt, "checkpw", lambda p, h: True)

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"
    assert "access_token" in resp.cookies


def test_login_sets_jwt_cookie(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server._bcrypt, "checkpw", lambda p, h: True)

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "secret"})

    cookie = resp.cookies.get("access_token")
    assert cookie is not None
    # TestClient may return cookie values with surrounding quotes (RFC 6265 quoting)
    assert "Bearer " in cookie


def test_login_wrong_password(client, mock_db, monkeypatch):
    mock_db.execute.return_value = make_result(one_or_none=_user_row())
    monkeypatch.setattr(api_server._bcrypt, "checkpw", lambda p, h: False)

    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})

    assert resp.status_code == 401


def test_login_unknown_email(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)

    resp = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "secret"}
    )

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
    assert body["role"] == REVIEWER.role
    assert "id" in body
    assert "full_name" in body


def test_me_tampered_token_rejected(client):
    client.cookies.set("access_token", "Bearer this.is.not.a.valid.jwt")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# ── API keys (Authorization header, external/programmatic callers) ────────────


def _api_key_row(active=True, tenant_active=True, expires_at=None):
    return (
        str(uuid.uuid4()),  # id
        "partner-x-integration",  # name
        active,  # is_active
        expires_at,  # expires_at
        TENANT_ID,  # tenant_id
        TENANT_SLUG,  # tenant_slug
        TENANT_NAME,  # tenant_name
        tenant_active,  # tenant_is_active
    )


def test_api_key_auth_success(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=_api_key_row())

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sdai_testsecret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "api_key"
    assert body["tenant_slug"] == TENANT_SLUG
    assert body["can_manage_access"] is False
    assert body["can_edit_extraction"] is False


def test_api_key_auth_unknown_key_rejected(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=None)

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sdai_bogus"})

    assert resp.status_code == 401


def test_api_key_auth_revoked_key_rejected(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=_api_key_row(active=False))

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sdai_testsecret"})

    assert resp.status_code == 401


def test_api_key_auth_deactivated_tenant_rejected(client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=_api_key_row(tenant_active=False))

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sdai_testsecret"})

    assert resp.status_code == 401


def test_api_key_auth_expired_key_rejected(client, mock_db):
    from datetime import datetime, timedelta, timezone

    expired = datetime.now(timezone.utc) - timedelta(days=1)
    mock_db.execute.return_value = make_result(one_or_none=_api_key_row(expires_at=expired))

    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer sdai_testsecret"})

    assert resp.status_code == 401


def test_api_key_never_satisfies_admin_guard(auth_client, monkeypatch):
    """role='api_key' must fail require_admin exactly like a plain reviewer -
    an external key can never reach an admin-only endpoint."""
    import api_server
    from auth import CurrentUser, get_current_user

    api_key_user = CurrentUser(
        id="key-1",
        email="api-key:partner-x",
        full_name=None,
        role="api_key",
        tenant_id=TENANT_ID,
        tenant_slug=TENANT_SLUG,
        tenant_name=TENANT_NAME,
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[],
        locale="en",
    )
    api_server.app.dependency_overrides[get_current_user] = lambda: api_key_user
    resp = auth_client.get("/api/admin/audit-log")
    assert resp.status_code == 403


def test_authorization_header_without_key_prefix_falls_through_to_cookie(client):
    """A non-API-key Authorization header (no 'sdai_' prefix, e.g. some other
    bearer token a caller happens to send) is ignored - the normal cookie
    flow still runs, and with no cookie set that's a 401, not a crash."""
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer some-other-token"})
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
