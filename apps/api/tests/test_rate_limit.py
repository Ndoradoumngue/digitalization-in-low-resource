"""Unit tests for rate_limit._user_or_ip key function."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from jose import jwt

from rate_limit import _user_or_ip

_SECRET = os.getenv("AUTH_SECRET_KEY", "dev-secret-key-change-in-production")
_ALG    = "HS256"


def _make_request(cookie: str = None, xff: str = None, client_host: str = "1.2.3.4"):
    """Build a minimal fake Request object."""
    req = MagicMock()
    req.cookies  = {"access_token": cookie} if cookie else {}
    req.headers  = {"X-Forwarded-For": xff} if xff else {}
    req.client   = MagicMock(host=client_host)
    return req


def _valid_token(user_id: str = "user-abc") -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    return "Bearer " + jwt.encode(
        {"sub": user_id, "exp": expire, "jti": "jti-123"},
        _SECRET, algorithm=_ALG,
    )


# ── Valid JWT ─────────────────────────────────────────────────────────────────

def test_valid_jwt_returns_user_key():
    req = _make_request(cookie=_valid_token("user-abc"))
    assert _user_or_ip(req) == "user:user-abc"


def test_valid_jwt_different_user_id():
    req = _make_request(cookie=_valid_token("user-xyz"))
    assert _user_or_ip(req) == "user:user-xyz"


# ── No cookie ─────────────────────────────────────────────────────────────────

def test_no_cookie_falls_back_to_client_ip():
    req = _make_request(client_host="10.0.0.5")
    assert _user_or_ip(req) == "ip:10.0.0.5"


# ── Invalid / tampered JWT ────────────────────────────────────────────────────

def test_tampered_token_falls_back_to_ip():
    req = _make_request(cookie="Bearer not.a.valid.jwt", client_host="9.9.9.9")
    assert _user_or_ip(req) == "ip:9.9.9.9"


def test_empty_bearer_token_falls_back_to_ip():
    req = _make_request(cookie="Bearer ", client_host="9.9.9.9")
    assert _user_or_ip(req) == "ip:9.9.9.9"


def test_expired_token_falls_back_to_ip():
    expired = datetime.now(timezone.utc) - timedelta(hours=1)
    token = "Bearer " + jwt.encode(
        {"sub": "user-abc", "exp": expired, "jti": "j1"},
        _SECRET, algorithm=_ALG,
    )
    req = _make_request(cookie=token, client_host="5.5.5.5")
    assert _user_or_ip(req) == "ip:5.5.5.5"


def test_token_missing_sub_falls_back_to_ip():
    token = "Bearer " + jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        _SECRET, algorithm=_ALG,
    )
    req = _make_request(cookie=token, client_host="6.6.6.6")
    assert _user_or_ip(req) == "ip:6.6.6.6"


# ── X-Forwarded-For ───────────────────────────────────────────────────────────

def test_xff_single_ip_used():
    req = _make_request(xff="203.0.113.1")
    assert _user_or_ip(req) == "ip:203.0.113.1"


def test_xff_first_ip_in_chain_used():
    req = _make_request(xff="203.0.113.1, 10.0.0.1, 192.168.1.1")
    assert _user_or_ip(req) == "ip:203.0.113.1"


def test_xff_takes_precedence_over_client_host():
    req = _make_request(xff="203.0.113.99", client_host="127.0.0.1")
    assert _user_or_ip(req) == "ip:203.0.113.99"


# ── No client object ──────────────────────────────────────────────────────────

def test_no_client_returns_unknown():
    req = _make_request()
    req.client = None
    assert _user_or_ip(req) == "ip:unknown"
