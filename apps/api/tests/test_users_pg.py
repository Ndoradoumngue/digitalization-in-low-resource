"""User management against a real PostgreSQL (GED Phase 9): creation with a
temporary password and the forced change, sessions ended by a reset or a
change, delegated managers who can't hand out more than they hold,
deactivation, and soft deletion (hidden, e-mail freed, shown as "user no
longer in system"). Real sign-ins - no dependency override. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_users_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import os

import api_server
import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

PW = "secret-pw-1"
ENV: dict = {}

# key -> (role, clearance, can_manage_users, can_share)
SEED = {
    "admin": ("admin", "secret", False, False),
    "manager": ("reviewer", "internal", True, True),
    "boss": ("reviewer", "secret", False, False),
}


async def _seed(engine):
    import bcrypt
    import ingest_router
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await ingest_router._init_db()
    pw_hash = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode()
    async with engine.begin() as conn:
        tid = (
            await conn.execute(
                text("SELECT id FROM sdai_tenants WHERE slug = 'default'")
            )
        ).scalar()
        for key, (role, clearance, manage, share) in SEED.items():
            ENV[key] = (
                await conn.execute(
                    text(
                        "INSERT INTO sdai_users (email, full_name, hashed_password, role, tenant_id,"
                        " clearance, can_manage_users, can_share)"
                        " VALUES (:e, :n, :pw, :r, :t, :c, :m, :s) RETURNING id::text"
                    ),
                    {
                        "e": f"{key}@example.org",
                        "n": key.capitalize(),
                        "pw": pw_hash,
                        "r": role,
                        "t": tid,
                        "c": clearance,
                        "m": manage,
                        "s": share,
                    },
                )
            ).scalar()


@pytest.fixture(scope="module")
def pg():
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    saved = (ingest_router.engine, ingest_router.SessionLocal)
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(_seed(engine))
    yield engine
    ingest_router.engine, ingest_router.SessionLocal = saved


def _sql(engine, sql: str, params: dict | None = None):
    from sqlalchemy import text

    async def run():
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.mappings().all() if result.returns_rows else None

    return asyncio.run(run())


@pytest.fixture()
def session(pg, dirs):
    """session(email, password) -> a TestClient signed in for real."""
    clients = []

    def login(email: str, password: str = PW, expect: int = 200) -> TestClient:
        client = TestClient(api_server.app)
        client.__enter__()
        clients.append(client)
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == expect, resp.text
        return client

    yield login
    for client in clients:
        client.__exit__(None, None, None)


def _create(client, email, **extra):
    body = {"email": email, "temporary_password": "temporary-1", **extra}
    return client.post("/api/admin/users", json=body)


# ── Creation, forced change, sessions ────────────────────────────────────────


def test_new_user_must_change_the_temporary_password(pg, session):
    admin = session("admin@example.org")
    resp = _create(admin, "Nadia@Example.org", full_name="Nadia", role="reviewer")
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["email"] == "nadia@example.org"  # normalised
    assert created["must_change_password"] is True
    assert _create(admin, "nadia@example.org").status_code == 409

    nadia = session("NADIA@example.org", "temporary-1")
    assert nadia.get("/api/auth/me").json()["must_change_password"] is True
    blocked = nadia.get("/api/db/documents")
    assert blocked.status_code == 403
    assert blocked.headers.get("X-Password-Change-Required") == "1"

    bad = nadia.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "my-own-pass-1"},
    )
    assert bad.status_code == 403
    same = nadia.post(
        "/api/auth/change-password",
        json={"current_password": "temporary-1", "new_password": "temporary-1"},
    )
    assert same.status_code == 422
    other_session = session("nadia@example.org", "temporary-1")
    ok = nadia.post(
        "/api/auth/change-password",
        json={"current_password": "temporary-1", "new_password": "my-own-pass-1"},
    )
    assert ok.status_code == 200, ok.text
    # this session got a fresh cookie and works; the other one ended
    assert nadia.get("/api/db/documents").status_code == 200
    assert other_session.get("/api/auth/me").status_code == 401
    session("nadia@example.org", "temporary-1", expect=401)
    session("nadia@example.org", "my-own-pass-1")

    actions = {r["action"] for r in _sql(pg, "SELECT action FROM sdai_audit_log")}
    assert {"user_created", "password_change_failed", "password_changed"} <= actions
    details = _sql(
        pg,
        "SELECT details::text AS d FROM sdai_audit_log WHERE action = 'user_created'",
    )
    assert "temporary-1" not in details[0]["d"]


def test_reset_password_ends_sessions(pg, session):
    admin = session("admin@example.org")
    user_id = _create(admin, "reset@example.org").json()["id"]
    user = session("reset@example.org", "temporary-1")
    user.post(
        "/api/auth/change-password",
        json={"current_password": "temporary-1", "new_password": "chosen-pass-1"},
    )
    assert user.get("/api/db/documents").status_code == 200
    resp = admin.post(
        f"/api/admin/users/{user_id}/reset-password",
        json={"temporary_password": "new-temp-22"},
    )
    assert resp.status_code == 200 and resp.json()["must_change_password"] is True
    assert user.get("/api/auth/me").status_code == 401
    again = session("reset@example.org", "new-temp-22")
    assert again.get("/api/db/documents").status_code == 403
    # own password: through change-password, not reset
    assert (
        admin.post(
            f"/api/admin/users/{ENV['admin']}/reset-password",
            json={"temporary_password": "whatever-123"},
        ).status_code
        == 409
    )


# ── Delegated manager: never more than they hold ─────────────────────────────


def test_delegated_manager_limits(pg, session):
    manager = session("manager@example.org")  # reviewer, internal, can_share
    assert manager.get("/api/admin/users").status_code == 200
    assert _create(manager, "a1@example.org", role="admin").status_code == 403
    assert (
        _create(manager, "a2@example.org", clearance="confidential").status_code == 403
    )
    assert _create(manager, "a3@example.org", can_manage_access=True).status_code == 403
    resp = _create(manager, "clerk@example.org", role="reader", can_share=True)
    assert resp.status_code == 201, resp.text
    clerk_id = resp.json()["id"]

    # no admin, nobody cleared above them, never their own rights
    assert (
        manager.patch(
            f"/api/admin/users/{ENV['admin']}", json={"full_name": "X"}
        ).status_code
        == 403
    )
    assert manager.post(f"/api/admin/users/{ENV['boss']}/deactivate").status_code == 403
    assert manager.patch(
        f"/api/admin/users/{ENV['manager']}", json={"can_manage_access": True}
    ).status_code in (403, 409)
    assert (
        manager.patch(
            f"/api/admin/users/{ENV['manager']}", json={"full_name": "Me"}
        ).status_code
        == 200
    )
    assert (
        manager.post(f"/api/admin/users/{ENV['manager']}/deactivate").status_code == 409
    )
    # deleting is for admins
    assert manager.delete(f"/api/admin/users/{clerk_id}").status_code == 403

    # a reviewer without the permission can't even list
    plain_id = _create(session("admin@example.org"), "plain@example.org").json()["id"]
    _sql(
        pg,
        "UPDATE sdai_users SET must_change_password = false WHERE id = CAST(:id AS uuid)",
        {"id": plain_id},
    )
    assert (
        session("plain@example.org", "temporary-1").get("/api/admin/users").status_code
        == 403
    )


# ── Deactivation ─────────────────────────────────────────────────────────────


def test_deactivate_and_reactivate(pg, session):
    admin = session("admin@example.org")
    user_id = _create(admin, "leaver@example.org").json()["id"]
    _sql(
        pg,
        "UPDATE sdai_users SET must_change_password = false WHERE id = CAST(:id AS uuid)",
        {"id": user_id},
    )
    leaver = session("leaver@example.org", "temporary-1")
    assert leaver.get("/api/auth/me").status_code == 200
    resp = admin.post(f"/api/admin/users/{user_id}/deactivate")
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert leaver.get("/api/auth/me").status_code == 401
    session("leaver@example.org", "temporary-1", expect=401)
    # still listed (deactivated), e-mail still taken
    listed = {u["id"]: u for u in admin.get("/api/admin/users").json()}
    assert listed[user_id]["is_active"] is False
    assert _create(admin, "leaver@example.org").status_code == 409
    assert (
        admin.post(f"/api/admin/users/{user_id}/activate").json()["is_active"] is True
    )
    session("leaver@example.org", "temporary-1")
    assert admin.post(f"/api/admin/users/{ENV['admin']}/deactivate").status_code == 409


# ── Soft deletion ────────────────────────────────────────────────────────────


def test_delete_hides_frees_email_and_keeps_history(pg, session):
    admin = session("admin@example.org")
    user_id = _create(admin, "gone@example.org", full_name="Gone Person").json()["id"]
    _sql(
        pg,
        "UPDATE sdai_users SET must_change_password = false WHERE id = CAST(:id AS uuid)",
        {"id": user_id},
    )
    gone = session("gone@example.org", "temporary-1")
    gone.get("/api/auth/me")
    group_id = admin.post("/api/admin/groups", json={"name": "Team"}).json()["id"]
    admin.post(f"/api/admin/users/{user_id}/groups", json={"group_id": group_id})

    assert admin.delete(f"/api/admin/users/{user_id}").status_code == 200
    assert gone.get("/api/auth/me").status_code == 401
    assert user_id not in {u["id"] for u in admin.get("/api/admin/users").json()}
    assert admin.post(f"/api/admin/users/{user_id}/activate").status_code == 404

    row = _sql(
        pg,
        "SELECT email, full_name, deleted_email, deleted_full_name, deleted_at, is_active"
        " FROM sdai_users WHERE id = CAST(:id AS uuid)",
        {"id": user_id},
    )[0]
    assert row["email"] == f"{user_id}@deleted.invalid"
    assert row["full_name"] is None and row["is_active"] is False
    assert row["deleted_email"] == "gone@example.org"
    assert row["deleted_full_name"] == "Gone Person"
    assert not _sql(
        pg,
        "SELECT 1 FROM sdai_user_groups WHERE user_id = CAST(:id AS uuid)",
        {"id": user_id},
    )

    # the audit log keeps the copy but shows the tombstone
    entries = admin.get("/api/admin/audit-log", params={"user_id": user_id}).json()[
        "items"
    ]
    assert entries and {e["user_email"] for e in entries} == {
        f"{user_id}@deleted.invalid"
    }

    # the address is free again - a different account
    new = _create(admin, "gone@example.org")
    assert new.status_code == 201 and new.json()["id"] != user_id

    # admins only, never yourself
    assert admin.delete(f"/api/admin/users/{ENV['admin']}").status_code == 409
