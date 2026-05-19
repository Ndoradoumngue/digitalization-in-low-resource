"""Tests for the X-Process-Time response-time middleware in api_server."""

import logging
import re

import pytest

import api_server


# ── Header presence and format ────────────────────────────────────────────────

def test_process_time_header_present(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert "X-Process-Time" in resp.headers


def test_process_time_header_is_valid_float(auth_client):
    resp = auth_client.get("/api/auth/me")
    value = resp.headers["X-Process-Time"]
    assert re.fullmatch(r"\d+\.\d{4}", value), f"Unexpected format: {value!r}"
    assert float(value) >= 0


def test_process_time_header_present_on_unauthenticated_request(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert "X-Process-Time" in resp.headers


# ── Slow-request warning ──────────────────────────────────────────────────────

def test_slow_request_logs_warning(auth_client, monkeypatch, caplog):
    # Patch time as seen by api_server so the middleware measures 3 s elapsed.
    # Only api_server.time is replaced — other modules are unaffected.
    from unittest.mock import MagicMock

    _calls = [0]

    def _fake_counter():
        _calls[0] += 1
        return 0.0 if _calls[0] == 1 else 3.0

    mock_time = MagicMock()
    mock_time.perf_counter = _fake_counter
    monkeypatch.setattr(api_server, "time", mock_time)

    with caplog.at_level(logging.WARNING, logger="api_server"):
        resp = auth_client.get("/api/auth/me")

    assert resp.status_code == 200
    assert "Slow request" in caplog.text


def test_fast_request_does_not_log_warning(auth_client, caplog):
    with caplog.at_level(logging.WARNING, logger="api_server"):
        resp = auth_client.get("/api/auth/me")

    assert resp.status_code == 200
    assert "Slow request" not in caplog.text
