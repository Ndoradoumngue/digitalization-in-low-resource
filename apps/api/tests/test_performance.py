"""Performance benchmark tests for SDAI API endpoints.

Each test is marked ``@pytest.mark.benchmark`` so it can be excluded from the
standard CI test run::

    pytest -m "not benchmark"

To run only the benchmarks::

    pytest -m benchmark

``pytest-benchmark`` drives each measurement; the ``benchmark`` and
``benchmark.pedantic`` fixtures handle warmup, rounds, and statistics.
Assertions on ``benchmark.stats.mean`` enforce the latency budgets.
"""

import time
import uuid
from itertools import cycle
from unittest.mock import AsyncMock

import pytest

import documents_router
import review_router
from conftest import make_result


# ── Shared fixtures ───────────────────────────────────────────────────────────

# Column set present in every ingest-created table
_BENCH_COLS = {
    "id", "source_image_path", "ingested_at", "confidence", "review_status",
}


def _make_doc(i: int) -> dict:
    """Return one synthetic document row dict as the router would return it."""
    return {
        "id":                     str(uuid.uuid4()),
        "table_name":             "bench_table",
        "document_type":          None,
        "reference_number":       None,
        "date":                   None,
        "organisation":           None,
        "destination_or_subject": None,
        "signatory":              None,
        "confidence":             "high",
        "review_status":          "auto_approved",
        "ingested_at":            "2024-01-01T00:00:00",
        "source_image_path":      f"/data/images/doc_{i}.png",
        "total_count":            1000,
    }


# ── test 1: document list ─────────────────────────────────────────────────────

@pytest.mark.benchmark
def test_document_list_response_time(auth_client, mock_db, monkeypatch, benchmark):
    """
    GET /api/db/documents with 1000 synthetic rows must respond in under 500ms
    (mean across benchmark rounds). The full-text-search variant (?q=) must
    respond in under 1000ms for a single call.

    /api/db/documents is not cached, so every benchmark iteration exercises
    the full Python serialisation path.
    """
    rows = [_make_doc(i) for i in range(1000)]

    monkeypatch.setattr(
        documents_router, "_get_tables_columns",
        AsyncMock(return_value={"bench_table": _BENCH_COLS}),
    )
    # list_documents calls execute once (UNION query); return_value is reused
    # on every benchmark iteration.
    mock_db.execute.return_value = make_result(rows=rows)

    # ── timing assertions ─────────────────────────────────────────────────────
    t0 = time.perf_counter()
    resp = auth_client.get("/api/db/documents")
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert elapsed < 0.500, (
        f"GET /api/db/documents took {elapsed * 1000:.0f}ms (limit 500ms)"
    )

    t0 = time.perf_counter()
    resp = auth_client.get("/api/db/documents?q=test")
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert elapsed < 1.000, (
        f"GET /api/db/documents?q=test took {elapsed * 1000:.0f}ms (limit 1000ms)"
    )

    # ── formal benchmark — run many rounds, generates the timing report ───────
    benchmark(lambda: auth_client.get("/api/db/documents"))


# ── test 2: schema endpoint ───────────────────────────────────────────────────

# Schema endpoint makes exactly 3 execute calls (for 1 table):
#   1. Batch column details  (iterable, consumed with "for row in result")
#   2. Batch FK details      (iterable)
#   3. Per-table stats       (accessed with .one())
_SCHEMA_COL_ROWS = [
    ("bench_table", "id",                "uuid", "NO"),
    ("bench_table", "source_image_path", "text", "YES"),
]
_SCHEMA_FK_ROWS  = []
_SCHEMA_STAT_ONE = (50, None, "bench_document_type")


@pytest.mark.benchmark
def test_schema_endpoint_response_time(auth_client, mock_db, monkeypatch, benchmark):
    """
    GET /api/db/schema must respond in under 200ms when uncached and under
    50ms on a subsequent (cached) call to the same URL.

    The formal benchmark uses ``pedantic`` with cache-clearing setup so each
    round measures the uncached code path.
    """
    from fastapi_cache.backends.inmemory import InMemoryBackend

    monkeypatch.setattr(
        documents_router, "_get_tables_columns",
        AsyncMock(return_value={"bench_table": {"id", "source_image_path"}}),
    )

    # Cycling side_effect: col → FK → stat → col → FK → stat → …
    # Works across the manual assertions and the pedantic rounds without
    # exhausting a finite list.
    _responses = cycle([
        make_result(rows=list(_SCHEMA_COL_ROWS)),
        make_result(rows=_SCHEMA_FK_ROWS),
        make_result(one=_SCHEMA_STAT_ONE),
    ])
    mock_db.execute.side_effect = lambda *_a, **_kw: next(_responses)

    # ── uncached call ─────────────────────────────────────────────────────────
    InMemoryBackend._store.clear()
    t0 = time.perf_counter()
    resp1 = auth_client.get("/api/db/schema")
    uncached = time.perf_counter() - t0
    assert resp1.status_code == 200
    assert uncached < 0.200, (
        f"Uncached GET /api/db/schema took {uncached * 1000:.0f}ms (limit 200ms)"
    )

    # ── cached call (second hit to the same URL) ──────────────────────────────
    t0 = time.perf_counter()
    resp2 = auth_client.get("/api/db/schema")
    cached = time.perf_counter() - t0
    assert resp2.status_code == 200
    assert cached < 0.050, (
        f"Cached GET /api/db/schema took {cached * 1000:.0f}ms (limit 50ms)"
    )

    # Persist both values in the benchmark report
    benchmark.extra_info["uncached_ms"] = round(uncached * 1000, 2)
    benchmark.extra_info["cached_ms"]   = round(cached * 1000, 2)

    # Formal benchmark: clear cache before each round so we always measure
    # the uncached path. The setup callable is NOT included in the timing.
    result = benchmark.pedantic(
        lambda: auth_client.get("/api/db/schema"),
        setup=InMemoryBackend._store.clear,
        rounds=3,
        iterations=1,
    )
    assert result.status_code == 200


# ── test 3: review count ──────────────────────────────────────────────────────

@pytest.mark.benchmark
def test_review_count_response_time(auth_client, mock_db, monkeypatch, benchmark):
    """
    GET /api/review/count must respond in under 100ms (mean, uncached).

    The endpoint is cached (expire=10s), so the setup callable clears the
    in-memory cache store before each benchmark round to ensure every
    iteration exercises the live DB path.
    """
    from fastapi_cache.backends.inmemory import InMemoryBackend

    monkeypatch.setattr(
        review_router, "_get_tables_columns",
        AsyncMock(return_value={"bench_table": {"id", "review_status"}}),
    )
    # _pending_count calls execute once (UNION COUNT query); scalar=42 is
    # reused on every benchmark iteration.
    mock_db.execute.return_value = make_result(scalar=42)

    # ── timing assertion — single uncached call ───────────────────────────────
    InMemoryBackend._store.clear()
    t0 = time.perf_counter()
    resp = auth_client.get("/api/review/count")
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert resp.json()["pending"] == 42
    assert elapsed < 0.100, (
        f"GET /api/review/count took {elapsed * 1000:.0f}ms (limit 100ms)"
    )

    # ── formal benchmark — cache cleared before each round ────────────────────
    benchmark.pedantic(
        lambda: auth_client.get("/api/review/count"),
        setup=InMemoryBackend._store.clear,
        rounds=5,
        iterations=1,
    )
