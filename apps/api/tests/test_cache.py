"""Unit tests for cache._path_key_builder and cache.invalidate_cache."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from cache import _path_key_builder, invalidate_cache

# ── _path_key_builder ─────────────────────────────────────────────────────────


def _fake_request(path: str = "/api/db/schema", query: str = "") -> MagicMock:
    req = MagicMock()
    req.url.path = path
    req.url.query = query
    return req


def _dummy_func():
    pass


_dummy_func.__module__ = "my_module"
_dummy_func.__name__ = "my_func"


def test_key_uses_path_when_request_present():
    req = _fake_request("/api/db/schema")
    key = _path_key_builder(_dummy_func, "schema", request=req, response=None, args=(), kwargs={})
    assert key == "schema:-:-:/api/db/schema"


def test_key_includes_query_string():
    req = _fake_request("/api/db/documents", "page=2&page_size=10")
    key = _path_key_builder(_dummy_func, "docs", request=req, response=None, args=(), kwargs={})
    assert key == "docs:-:-:/api/db/documents?page=2&page_size=10"


def test_key_no_query_string_omits_questionmark():
    req = _fake_request("/api/review/count", "")
    key = _path_key_builder(_dummy_func, "review", request=req, response=None, args=(), kwargs={})
    assert "?" not in key
    assert key == "review:-:-:/api/review/count"


def test_key_fallback_when_no_request():
    key = _path_key_builder(_dummy_func, "ns", request=None, response=None, args=(), kwargs={})
    assert key == "ns:-:-:my_module.my_func"


def test_key_scoped_by_tenant():
    """Different tenants requesting the same path must get different cache
    keys - the fix for the cross-tenant cache leak (two tenants sharing an
    instance must never see each other's cached /api/db/types etc.)."""
    from dataclasses import dataclass

    @dataclass
    class _FakeUser:
        tenant_slug: str
        id: str = "u1"

    req = _fake_request("/api/db/schema")
    k_land = _path_key_builder(
        _dummy_func,
        "schema",
        request=req,
        response=None,
        args=(),
        kwargs={"current_user": _FakeUser("land")},
    )
    k_oil = _path_key_builder(
        _dummy_func,
        "schema",
        request=req,
        response=None,
        args=(),
        kwargs={"current_user": _FakeUser("oil")},
    )
    assert k_land != k_oil
    assert k_land == "schema:land:u1:/api/db/schema"


def test_different_paths_produce_different_keys():
    req1 = _fake_request("/api/db/schema")
    req2 = _fake_request("/api/db/types")
    k1 = _path_key_builder(_dummy_func, "x", request=req1, response=None, args=(), kwargs={})
    k2 = _path_key_builder(_dummy_func, "x", request=req2, response=None, args=(), kwargs={})
    assert k1 != k2


def test_key_scoped_by_user():
    """Two users of one tenant see different things (grants, clearance,
    role) - each gets their own cache entry (GED Phase 1)."""
    from dataclasses import dataclass

    @dataclass
    class _FakeUser:
        tenant_slug: str
        id: str

    req = _fake_request("/api/review/count")
    keys = {
        _path_key_builder(
            _dummy_func, "review", request=req, response=None, args=(),
            kwargs={"current_user": _FakeUser("land", uid)},
        )
        for uid in ("reader-1", "reviewer-2")
    }
    assert len(keys) == 2


def test_same_path_same_key_regardless_of_caller():
    req = _fake_request("/api/db/schema")
    k1 = _path_key_builder(_dummy_func, "schema", request=req, response=None, args=(), kwargs={})
    k2 = _path_key_builder(lambda: None, "schema", request=req, response=None, args=(), kwargs={})
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


# ── first_in_window (audit dedupe) ────────────────────────────────────────────


def test_first_in_window_dedupes_until_expiry(monkeypatch):
    import asyncio

    import cache

    cache._local_windows.clear()
    now = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(cache.FastAPICache, "get_backend", lambda: object())  # no redis
    assert asyncio.run(cache.first_in_window("k", 300)) is True
    assert asyncio.run(cache.first_in_window("k", 300)) is False
    assert asyncio.run(cache.first_in_window("other", 300)) is True
    now[0] += 301
    assert asyncio.run(cache.first_in_window("k", 300)) is True


def test_first_in_window_uses_redis_set_nx(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import cache

    backend = MagicMock()
    backend.redis.set = AsyncMock(side_effect=[True, None])
    monkeypatch.setattr(cache.FastAPICache, "get_backend", lambda: backend)
    assert asyncio.run(cache.first_in_window("k", 300)) is True
    assert asyncio.run(cache.first_in_window("k", 300)) is False
    backend.redis.set.assert_awaited_with("sdai:window:k", b"1", ex=300, nx=True)
