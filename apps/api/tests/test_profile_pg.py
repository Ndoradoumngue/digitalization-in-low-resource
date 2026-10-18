"""My profile against a real PostgreSQL: name / job title / phone, the
picture re-encoded to a square JPEG without metadata, served to colleagues
of the same tenant only, removed with a deleted account. Real sign-ins - no
dependency override. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_profile_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import io
import os

import api_server
import pytest
from fastapi.testclient import TestClient
from PIL import Image

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

PW = "secret-pw-1"
ENV: dict = {}

# key -> (tenant, role)
SEED = {
    "admin": ("default", "admin"),
    "reader": ("default", "reader"),
    "outsider": ("other", "reviewer"),
    "leaver": ("default", "reviewer"),
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
        await conn.execute(
            text("INSERT INTO sdai_tenants (slug, name) VALUES ('other', 'Other')")
        )
        for key, (tenant, role) in SEED.items():
            ENV[key] = (
                await conn.execute(
                    text(
                        "INSERT INTO sdai_users (email, full_name, hashed_password, role, tenant_id)"
                        " SELECT :e, :n, :pw, :r, id FROM sdai_tenants WHERE slug = :t"
                        " RETURNING id::text"
                    ),
                    {
                        "e": f"{key}@example.org",
                        "n": key.capitalize(),
                        "pw": pw_hash,
                        "r": role,
                        "t": tenant,
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


@pytest.fixture()
def session(pg, dirs):
    clients = []

    def login(email: str) -> TestClient:
        client = TestClient(api_server.app)
        client.__enter__()
        clients.append(client)
        resp = client.post("/api/auth/login", json={"email": email, "password": PW})
        assert resp.status_code == 200, resp.text
        return client

    yield login
    for client in clients:
        client.__exit__(None, None, None)


def _png(width=600, height=300, color=(200, 30, 30, 128)) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"  # Make
    out = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(out, format="PNG", exif=exif)
    return out.getvalue()


def test_profile_fields_are_saved_and_returned_by_me(session):
    reader = session("reader@example.org")
    resp = reader.patch(
        "/api/account/profile",
        json={
            "full_name": "  Ada Reader ",
            "job_title": "Archivist",
            "phone": "+1 555 0100",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "full_name": "Ada Reader",
        "job_title": "Archivist",
        "phone": "+1 555 0100",
    }
    me = reader.get("/api/auth/me").json()
    assert (me["full_name"], me["job_title"], me["phone"]) == (
        "Ada Reader",
        "Archivist",
        "+1 555 0100",
    )
    # Only what is sent changes; an empty string clears.
    assert reader.patch("/api/account/profile", json={"phone": ""}).status_code == 200
    me = reader.get("/api/auth/me").json()
    assert me["phone"] is None and me["job_title"] == "Archivist"
    # Not editable here: who you are and what you may do.
    reader.patch(
        "/api/account/profile", json={"email": "x@example.org", "role": "admin"}
    )
    me = reader.get("/api/auth/me").json()
    assert me["email"] == "reader@example.org" and me["role"] == "reader"
    assert (
        reader.patch("/api/account/profile", json={"phone": "1" * 51}).status_code
        == 422
    )


def test_picture_is_reencoded_and_shared_within_the_tenant_only(session):
    reader = session("reader@example.org")
    assert reader.get("/api/auth/me").json()["avatar_url"] is None

    resp = reader.put(
        "/api/account/avatar", files={"file": ("me.png", _png(), "image/png")}
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["avatar_url"]
    assert url.startswith(f"/api/users/{ENV['reader']}/avatar?v=")
    assert reader.get("/api/auth/me").json()["avatar_url"] == url

    admin = session("admin@example.org")
    got = admin.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["cache-control"].startswith("private")
    img = Image.open(io.BytesIO(got.content))
    assert (img.format, img.size, img.mode) == ("JPEG", (256, 256), "RGB")
    assert b"SecretCamera" not in got.content and not img.getexif()
    users = {u["id"]: u for u in admin.get("/api/admin/users").json()}
    assert users[ENV["reader"]]["avatar_url"] == url

    outsider = session("outsider@example.org")
    assert outsider.get(url).status_code == 404
    assert outsider.get("/api/users/not-a-uuid/avatar").status_code == 404

    assert reader.delete("/api/account/avatar").status_code == 200
    assert reader.get("/api/auth/me").json()["avatar_url"] is None
    assert admin.get(url).status_code == 404


def test_what_is_not_a_picture_is_refused(session):
    reader = session("reader@example.org")
    for name, content in [
        ("x.png", b"%PDF-1.7 not an image"),
        ("x.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 50),  # truncated
        ("x.bmp", _bmp()),
    ]:
        resp = reader.put("/api/account/avatar", files={"file": (name, content)})
        assert resp.status_code == 422, (name, resp.text)
    big = b"\0" * (5 * 1024 * 1024 + 1)
    assert (
        reader.put("/api/account/avatar", files={"file": ("x.png", big)}).status_code
        == 413
    )


def _bmp() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, format="BMP")
    return out.getvalue()


def test_deleting_an_account_removes_its_picture(pg, session):
    from sqlalchemy import text

    leaver = session("leaver@example.org")
    leaver.patch("/api/account/profile", json={"job_title": "Clerk", "phone": "123"})
    url = leaver.put(
        "/api/account/avatar", files={"file": ("me.png", _png(), "image/png")}
    ).json()["avatar_url"]
    admin = session("admin@example.org")
    assert admin.get(url).status_code == 200

    assert admin.delete(f"/api/admin/users/{ENV['leaver']}").status_code == 200
    assert admin.get(url).status_code == 404

    async def row():
        async with pg.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT job_title, phone, avatar, avatar_updated_at FROM sdai_users"
                        " WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": ENV["leaver"]},
                )
            ).one()

    assert tuple(asyncio.run(row())) == (None, None, None, None)
