"""create_tenant.py / create_admin.py against a real PostgreSQL (GED Phase 9): first-run.sh
names the auto-seeded 'default' tenant with `--slug default` - it is updated,
never created. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_create_tenant_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]


def _sql(sql: str):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    async def run():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            result = await conn.execute(text(sql))
            rows = result.mappings().all() if result.returns_rows else None
        await engine.dispose()
        return rows

    return asyncio.run(run())


@pytest.fixture()
def fresh_db(monkeypatch):
    import create_tenant
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(ingest_router, "engine", engine)
    monkeypatch.setattr(
        ingest_router,
        "SessionLocal",
        async_sessionmaker(engine, expire_on_commit=False),
    )
    _sql("DROP SCHEMA public CASCADE")
    _sql("CREATE SCHEMA public")
    asyncio.run(ingest_router._init_db())
    monkeypatch.setattr(create_tenant, "DATABASE_URL", TEST_DATABASE_URL)
    return create_tenant


def _run(create_tenant, slug: str, name: str, locale: str | None):
    asyncio.run(create_tenant.main(slug, name, None, None, None, None, locale))


def test_default_tenant_is_named_not_duplicated(fresh_db):
    _run(fresh_db, "default", "Organisation test", "fr")
    _run(fresh_db, "default", "Organisation test 2", "en")
    rows = _sql("SELECT slug, name, locale FROM sdai_tenants ORDER BY slug")
    assert [dict(r) for r in rows] == [
        {"slug": "default", "name": "Organisation test 2", "locale": "en"}
    ]
    _run(fresh_db, "land", "Land office", None)
    assert [r["slug"] for r in _sql("SELECT slug FROM sdai_tenants ORDER BY slug")] == [
        "default",
        "land",
    ]


def test_default_tenant_is_never_created(fresh_db):
    # a cloud node that joined a deployment has no auto-seeded tenant
    _sql("UPDATE sdai_tenants SET slug = 'peer_org' WHERE slug = 'default'")
    with pytest.raises(SystemExit):
        _run(fresh_db, "default", "Organisation test", "fr")
    assert [r["slug"] for r in _sql("SELECT slug FROM sdai_tenants")] == ["peer_org"]


def test_create_admin_gives_admins_every_clearance(fresh_db, monkeypatch):
    import create_admin

    monkeypatch.setattr(create_admin, "DATABASE_URL", TEST_DATABASE_URL)
    for email, role in (("a@example.org", "admin"), ("r@example.org", "reviewer")):
        asyncio.run(create_admin.main(email, "secret-pw-1", "", "default", role))
    rows = _sql("SELECT email, role, clearance FROM sdai_users ORDER BY email")
    assert [(r["email"], r["clearance"]) for r in rows] == [
        ("a@example.org", "secret"),
        ("r@example.org", "internal"),
    ]
    # re-running for a reviewer keeps the clearance set in the Users page
    _sql(
        "UPDATE sdai_users SET clearance = 'confidential' WHERE email = 'r@example.org'"
    )
    asyncio.run(
        create_admin.main("r@example.org", "secret-pw-2", "", "default", "reviewer")
    )
    assert (
        _sql("SELECT clearance FROM sdai_users WHERE email = 'r@example.org'")[0][
            "clearance"
        ]
        == "confidential"
    )
