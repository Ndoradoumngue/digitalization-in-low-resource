"""Unit tests for cache._path_key_builder and cache.invalidate_cache."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cache import _path_key_builder, invalidate_cache


# ── _path_key_builder ─────────────────────────────────────────────────────────

def _fake_request(path: str = "/api/db/schema", query: str = "") -> MagicMock:
    req = MagicMock()
    req.url.path  = path
    req.url.query = query
    return req


def _dummy_func():
    pass

_dummy_func.__module__ = "my_module"
_dummy_func.__name__   = "my_func"


def test_key_uses_path_when_request_present():
    req = _fake_request("/api/db/schema")
    key = _path_key_builder(_dummy_func, "schema",
                             request=req, response=None,
                             args=(), kwargs={})
    assert key == "schema:/api/db/schema"


def test_key_includes_query_string():
    req = _fake_request("/api/db/documents", "page=2&page_size=10")
    key = _path_key_builder(_dummy_func, "docs",
                             request=req, response=None,
                             args=(), kwargs={})
    assert key == "docs:/api/db/documents?page=2&page_size=10"


def test_key_no_query_string_omits_questionmark():
    req = _fake_request("/api/review/count", "")
    key = _path_key_builder(_dummy_func, "review",
                             request=req, response=None,
                             args=(), kwargs={})
    assert "?" not in key
    assert key == "review:/api/review/count"


def test_key_fallback_when_no_request():
    key = _path_key_builder(_dummy_func, "ns",
                             request=None, response=None,
                             args=(), kwargs={})
    assert key == "ns:my_module.my_func"


def test_different_paths_produce_different_keys():
    req1 = _fake_request("/api/db/schema")
    req2 = _fake_request("/api/db/types")
    k1 = _path_key_builder(_dummy_func, "x", request=req1,
                            response=None, args=(), kwargs={})
    k2 = _path_key_builder(_dummy_func, "x", request=req2,
                            response=None, args=(), kwargs={})
    assert k1 != k2


def test_same_path_same_key_regardless_of_caller():
    req = _fake_request("/api/db/schema")
    k1 = _path_key_builder(_dummy_func, "schema",
                            request=req, response=None, args=(), kwargs={})
    k2 = _path_key_builder(lambda: None, "schema",
                            request=req, response=None, args=(), kwargs={})
    assert k1 == k2


# ── invalidate_cache ──────────────────────────────────────────────────────────

def test_invalidate_cache_swallows_exceptions():
    # FastAPICache.clear raises; invalidate_cache must not propagate
    with patch("cache.FastAPICache") as mock_fc:
        mock_fc.clear = AsyncMock(side_effect=RuntimeError("redis down"))
        # Should not raise
        asyncio.run(invalidate_cache("schema"))


def test_invalidate_cache_calls_clear_per_namespace():
    with patch("cache.FastAPICache") as mock_fc:
        mock_fc.clear = AsyncMock()
        asyncio.run(invalidate_cache("schema", "types", "review"))
        assert mock_fc.clear.await_count == 3


def test_invalidate_cache_no_namespaces_is_noop():
    with patch("cache.FastAPICache") as mock_fc:
        mock_fc.clear = AsyncMock()
        asyncio.run(invalidate_cache())
        mock_fc.clear.assert_not_awaited()
