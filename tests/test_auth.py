"""Tests for /api/auth/* endpoints."""

import pytest
from fastapi.testclient import TestClient


def test_login_success(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "changeme"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "admin"}
    assert "access_token" in resp.cookies


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_wrong_username(client):
    resp = client.post("/api/auth/login", json={"username": "hacker", "password": "changeme"})
    assert resp.status_code == 401


def test_login_missing_fields(client):
    resp = client.post("/api/auth/login", json={"username": "admin"})
    assert resp.status_code == 422


def test_me_unauthenticated(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_authenticated(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json() == {"username": "admin"}


def test_me_rejects_tampered_token(client):
    client.cookies.set("access_token", "Bearer invalidtoken")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_logout_returns_ok(auth_client):
    resp = auth_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"message": "logged out"}
