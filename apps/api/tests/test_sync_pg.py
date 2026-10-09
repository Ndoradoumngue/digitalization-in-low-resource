"""Local/cloud sync against a real PostgreSQL (GED Phase 8). Opt-in, like
test_access_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_sync_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import json
import os
import uuid

import pytest
from sync.hlc import is_hlc, parse_hlc

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

T = "t_default_invoice"
STATE: dict = {}


def _id() -> str:
    return str(uuid.uuid4())


async def _reset_and_init(engine):
    import ingest_router
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    # A document table and a user that exist before capture is installed -
    # the seed must carry them. First startup creates the schema, then the
    # rows are inserted with the capture triggers still absent.
    await ingest_router._init_db()
    async with engine.begin() as conn:
        await conn.execute(
            text("DROP TRIGGER IF EXISTS sdai_capture_change ON sdai_users")
        )
        STATE["tenant"] = str(
            (
                await conn.execute(
                    text("SELECT id FROM sdai_tenants WHERE slug = 'default'")
                )
            ).scalar()
        )
        STATE["pre_user"] = _id()
        await conn.execute(
            text(
                "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                " VALUES (CAST(:id AS uuid), 'pre@example.com', 'x', 'admin', CAST(:t AS uuid))"
            ),
            {"id": STATE["pre_user"], "t": STATE["tenant"]},
        )
        await conn.execute(text("UPDATE sdai_sync_state SET seeded_at = NULL"))
    # Second startup: triggers reinstalled, changelog seeded once.
    await ingest_router._init_db()


@pytest.fixture(scope="module")
def engine():
    import ingest_router
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    event.listen(eng.sync_engine, "connect", ingest_router._set_node_id_on_connect)
    saved = (ingest_router.engine, ingest_router.SessionLocal)
    ingest_router.engine = eng
    ingest_router.SessionLocal = async_sessionmaker(eng, expire_on_commit=False)
    asyncio.run(_reset_and_init(eng))
    yield eng
    ingest_router.engine, ingest_router.SessionLocal = saved


def run(coro):
    return asyncio.run(coro)


async def _q(engine, sql, params=None):
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        try:
            return [dict(r) for r in result.mappings().all()]
        except Exception:
            return []


async def _changes(engine, table, since=0):
    return await _q(
        engine,
        "SELECT * FROM sdai_sync_changelog WHERE table_name = :t AND id > :s ORDER BY id",
        {"t": table, "s": since},
    )


async def _max_id(engine):
    rows = await _q(engine, "SELECT COALESCE(max(id), 0) AS m FROM sdai_sync_changelog")
    return rows[0]["m"]


# ── 8.1 change capture ────────────────────────────────────────────────────────


def test_seed_carries_rows_that_predate_capture_once(engine):
    seeded = run(_changes(engine, "sdai_users"))
    assert [c["pk"] for c in seeded] == [{"id": STATE["pre_user"]}]
    assert seeded[0]["op"] == "insert"
    assert seeded[0]["row_data"]["email"] == "pre@example.com"
    tenants = run(_changes(engine, "sdai_tenants"))
    assert tenants[0]["pk"] == {"id": STATE["tenant"]}
    # Seed order follows foreign keys: the tenant comes before its user.
    assert tenants[0]["id"] < seeded[0]["id"]
    clock = run(
        _q(
            engine,
            "SELECT field_hlcs FROM sdai_sync_row_clock WHERE table_name = 'sdai_users'",
        )
    )
    assert clock == [{"field_hlcs": {"*": seeded[0]["hlc"]}}]

    # Another startup does not seed again.
    import ingest_router

    before = run(_max_id(engine))
    run(ingest_router._init_db())
    assert run(_changes(engine, "sdai_users")) == seeded
    assert run(_max_id(engine)) >= before


def test_hlc_stamp_format_and_floor(engine):
    rows = run(_q(engine, "SELECT sdai_hlc_now() AS a, sdai_hlc_now() AS b"))
    a, b = rows[0]["a"], rows[0]["b"]
    assert is_hlc(a) and is_hlc(b) and a < b
    assert parse_hlc(a).node == "local"
    far = 9_000_000_000_000
    run(_q(engine, "UPDATE sdai_sync_state SET hlc_floor_ms = :f", {"f": far}))
    try:
        stamp = run(_q(engine, "SELECT sdai_hlc_now() AS h"))[0]["h"]
        assert parse_hlc(stamp).ms == far
    finally:
        run(_q(engine, "UPDATE sdai_sync_state SET hlc_floor_ms = 0"))


def test_insert_update_delete_captured_with_pk_changed_cols_and_base(engine):
    since = run(_max_id(engine))
    gid = _id()
    run(
        _q(
            engine,
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'HR')",
            {"g": gid, "t": STATE["tenant"]},
        )
    )
    run(
        _q(
            engine,
            "UPDATE sdai_groups SET name = 'RH' WHERE id = CAST(:g AS uuid)",
            {"g": gid},
        )
    )
    # An update that changes nothing (only the touch columns move) is not a change.
    run(
        _q(
            engine,
            "UPDATE sdai_groups SET name = 'RH' WHERE id = CAST(:g AS uuid)",
            {"g": gid},
        )
    )
    run(_q(engine, "DELETE FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}))

    ins, upd, dele = run(_changes(engine, "sdai_groups", since))
    assert [c["op"] for c in (ins, upd, dele)] == ["insert", "update", "delete"]
    assert all(c["pk"] == {"id": gid} for c in (ins, upd, dele))
    assert ins["row_data"]["name"] == "HR" and ins["origin_node"] == "local"
    assert upd["row_data"] == {"name": "RH"}
    # The update overwrote the insert's clock - that is its base.
    assert upd["base_hlcs"] == {"name": ins["hlc"]}
    assert dele["row_data"] is None
    assert ins["hlc"] < upd["hlc"] < dele["hlc"]
    # The row's own hlc column carries the stamp of its last change.
    assert ins["row_data"]["hlc"] == ins["hlc"]
    assert (
        run(
            _q(
                engine,
                "SELECT 1 FROM sdai_sync_row_clock WHERE pk = :pk",
                {"pk": json.dumps({"id": gid})},
            )
        )
        == []
    )


def test_row_clock_tracks_last_change_per_field(engine):
    sid = _id()
    run(
        _q(
            engine,
            "INSERT INTO sdai_series (id, tenant_id, name) VALUES (CAST(:s AS uuid), CAST(:t AS uuid), 'A')",
            {"s": sid, "t": STATE["tenant"]},
        )
    )
    run(
        _q(
            engine,
            "UPDATE sdai_series SET description = 'd1' WHERE id = CAST(:s AS uuid)",
            {"s": sid},
        )
    )
    run(
        _q(
            engine,
            "UPDATE sdai_series SET name = 'B' WHERE id = CAST(:s AS uuid)",
            {"s": sid},
        )
    )
    run(
        _q(
            engine,
            "UPDATE sdai_series SET description = 'd2' WHERE id = CAST(:s AS uuid)",
            {"s": sid},
        )
    )
    ins, d1, name, d2 = run(_changes(engine, "sdai_series"))[-4:]
    assert d2["base_hlcs"] == {"description": d1["hlc"]}
    assert name["base_hlcs"] == {"name": ins["hlc"]}
    clock = run(
        _q(
            engine,
            "SELECT field_hlcs FROM sdai_sync_row_clock WHERE table_name = 'sdai_series' AND pk = :pk",
            {"pk": json.dumps({"id": sid})},
        )
    )[0]["field_hlcs"]
    assert clock == {"*": ins["hlc"], "description": d2["hlc"], "name": name["hlc"]}


def test_applying_sync_is_not_captured(engine):
    from sqlalchemy import text

    async def go():
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL sdai.applying_sync = on"))
            await conn.execute(
                text(
                    "INSERT INTO sdai_groups (tenant_id, name, origin_node, hlc)"
                    " VALUES (CAST(:t AS uuid), 'from-peer', 'cloud', '0000000000001:00000:cloud')"
                ),
                {"t": STATE["tenant"]},
            )

    since = run(_max_id(engine))
    run(go())
    assert run(_max_id(engine)) == since
    row = run(
        _q(engine, "SELECT origin_node, hlc FROM sdai_groups WHERE name = 'from-peer'")
    )[0]
    # The touch trigger keeps the values the change arrived with.
    assert row == {"origin_node": "cloud", "hlc": "0000000000001:00000:cloud"}


def test_composite_key_and_insert_only_tables(engine):
    gid = _id()
    run(
        _q(
            engine,
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'G2')",
            {"g": gid, "t": STATE["tenant"]},
        )
    )
    since = run(_max_id(engine))
    run(
        _q(
            engine,
            "INSERT INTO sdai_user_groups (user_id, group_id) VALUES (CAST(:u AS uuid), CAST(:g AS uuid))",
            {"u": STATE["pre_user"], "g": gid},
        )
    )
    run(
        _q(
            engine,
            "DELETE FROM sdai_user_groups WHERE group_id = CAST(:g AS uuid)",
            {"g": gid},
        )
    )
    ug = run(_changes(engine, "sdai_user_groups", since))
    assert [c["op"] for c in ug] == ["insert", "delete"]
    assert ug[0]["pk"] == {"user_id": STATE["pre_user"], "group_id": gid}

    run(
        _q(
            engine,
            "INSERT INTO sdai_token_blocklist (jti, expired_at) VALUES ('jti-1', now() - interval '1 day')",
        )
    )
    run(_q(engine, "DELETE FROM sdai_token_blocklist WHERE jti = 'jti-1'"))
    bl = run(_changes(engine, "sdai_token_blocklist", since))
    assert [(c["op"], c["pk"]) for c in bl] == [("insert", {"jti": "jti-1"})]

    run(_q(engine, "INSERT INTO sdai_audit_log (action) VALUES ('test_action')"))
    audit = run(_changes(engine, "sdai_audit_log", since))
    assert audit[-1]["op"] == "insert"
    # Captured after the chain trigger: the chain values travel with the row.
    assert audit[-1]["row_data"]["row_hash"] and audit[-1]["row_data"]["chain_seq"]


def test_node_local_tables_not_captured(engine):
    import ingest_router

    async def go():
        async with engine.begin() as conn:
            await ingest_router._next_record_id(conn, STATE["tenant"], "default")

    since = run(_max_id(engine))
    run(go())
    assert run(_max_id(engine)) == since
    tables = {
        r["table_name"]
        for r in run(_q(engine, "SELECT DISTINCT table_name FROM sdai_sync_changelog"))
    }
    assert not tables & {
        "sdai_nodes",
        "sdai_record_id_counters",
        "sdai_audit_chain_heads",
    }


def test_dynamic_table_ddl_events_and_row_capture(engine):
    import ingest_router
    from sqlalchemy import text

    since = run(_max_id(engine))

    async def create():
        async with engine.begin() as conn:
            assert (
                await ingest_router._ensure_table(
                    conn, T, {"reference_number": "x", "items": ["a"]}
                )
                == "created"
            )
            assert (
                await ingest_router._ensure_table(conn, T, {"reference_number": "x"})
                == "unchanged"
            )
            assert (
                await ingest_router._ensure_table(
                    conn, T, {"amount": "1", "paid": True}
                )
                == "altered"
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{T}" (id, reference_number, items) VALUES (CAST(:i AS uuid), :r, :items)'
                ),
                {
                    "i": STATE.setdefault("doc", _id()),
                    "r": "R-1",
                    "items": json.dumps(["a"]),
                },
            )

    run(create())
    changes = run(_changes(engine, T, since))
    assert [c["op"] for c in changes] == ["ddl", "ddl", "insert"]
    created, altered, row = changes
    assert created["row_data"]["columns"]["reference_number"] == "text"
    assert created["row_data"]["columns"]["items"] == "jsonb"
    assert "amount" not in created["row_data"]["columns"]
    assert altered["row_data"]["columns"]["paid"] == "boolean"
    assert row["pk"] == {"id": STATE["doc"]} and row["row_data"]["items"] == ["a"]
