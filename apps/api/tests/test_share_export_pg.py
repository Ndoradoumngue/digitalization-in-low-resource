"""Original download, share links and selective export against a real
PostgreSQL (GED Phase 5). Opt-in, like test_access_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_share_export_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import hashlib
import io
import json
import os
import uuid
from datetime import datetime, timezone

import api_server
import pytest
from auth import CurrentUser, get_current_user
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

T = "t_default_invoice"
PNG = b"\x89PNG\r\n\x1a\n"


def _id() -> str:
    return str(uuid.uuid4())


def _pdf_bytes(label: str) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, "PDF", title=label)
    return buf.getvalue()


U = {k: _id() for k in ("admin", "rev", "reader", "sharer", "outsider", "lowclear")}
D = {
    k: _id()
    for k in (
        "pdf",
        "png",
        "conf",
        "secret",
        "readonly",
        "hidden",
        "pending",
        "trashed",
    )
}
TENANT: dict = {}
FILES: dict = {}


def _user(key, role, **flags) -> CurrentUser:
    return CurrentUser(
        id=U[key],
        email=f"{key}@example.com",
        full_name=key,
        role=role,
        tenant_id=TENANT["id"],
        tenant_slug="default",
        tenant_name="Default",
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[],
        locale="en",
        can_share=flags.get("can_share", False),
        clearance=flags.get("clearance", "secret"),
    )


def ADMIN():
    return _user("admin", "admin")


def REV():
    return _user("rev", "reviewer", can_share=True)


def READER():
    return _user("reader", "reader")


def SHARER():
    return _user("sharer", "reader", can_share=True)


def OUTSIDER():
    return _user("outsider", "reviewer", can_share=True)


def LOWCLEAR():
    return _user("lowclear", "reviewer", can_share=True, clearance="internal")


_ROLES = {
    "admin": "admin",
    "rev": "reviewer",
    "reader": "reader",
    "sharer": "reader",
    "outsider": "reviewer",
    "lowclear": "reviewer",
}


async def _seed(engine, files_dir):
    import ingest_router
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await ingest_router._init_db()

    async with engine.begin() as conn:
        TENANT["id"] = str(
            (
                await conn.execute(
                    text("SELECT id FROM sdai_tenants WHERE slug = 'default'")
                )
            ).scalar()
        )
        for key, role in _ROLES.items():
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id,"
                    " can_share, clearance)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid), :s, :c)"
                ),
                {
                    "id": U[key],
                    "e": f"{key}@example.com",
                    "r": role,
                    "t": TENANT["id"],
                    "s": key in ("rev", "sharer", "outsider", "lowclear"),
                    "c": "internal" if key == "lowclear" else "secret",
                },
            )
        await ingest_router._ensure_table(
            conn, T, {"document_type": "x", "reference_number": "x", "amount": "x"}
        )

        async def doc(key, path, **cols):
            content = open(path, "rb").read()
            base = {
                "id": D[key],
                "source_image_path": path,
                "document_type": "x",
                "reference_number": f"ref {key}",
                "amount": "10",
                "review_status": "approved",
                "original_path": path,
                "original_hash": hashlib.sha256(content).hexdigest(),
                "record_id": f"INV-2026-{list(D).index(key) + 1:06d}",
            }
            base.update(cols)
            names = ", ".join(base)
            values = ", ".join(f":{k}" for k in base)
            await conn.execute(
                text(f'INSERT INTO "{T}" ({names}) VALUES ({values})'), base
            )

        for key in D:
            if key == "png":
                p = files_dir / "scan été.png"
                p.write_bytes(PNG + b"png")
            elif key == "pending":
                p = files_dir / "never-arrived.pdf"  # not on disk
            else:
                p = files_dir / f"{key}.pdf"
                p.write_bytes(_pdf_bytes(key))
            FILES[key] = str(p)
            if key == "pending":
                await conn.execute(
                    text(
                        f'INSERT INTO "{T}" (id, source_image_path, document_type, review_status,'
                        " original_path) VALUES (:id, :p, 'x', 'approved', :p)"
                    ),
                    {"id": D[key], "p": str(p)},
                )
                continue
            extra = {}
            if key == "conf":
                extra["confidentiality"] = "confidential"
            elif key == "secret":
                extra["confidentiality"] = "secret"
            elif key == "trashed":
                extra["deleted_at"] = datetime.now(timezone.utc)
            await doc(key, str(p), **extra)

        async def grant(doc_key, user_key, permission):
            await conn.execute(
                text(
                    "INSERT INTO sdai_document_access (tenant_id, scope, table_name, document_id,"
                    " grantee_type, grantee_id, permission)"
                    " VALUES (CAST(:t AS uuid), 'document', :tb, CAST(:d AS uuid), 'user',"
                    "         CAST(:u AS uuid), :p)"
                ),
                {
                    "t": TENANT["id"],
                    "tb": T,
                    "d": D[doc_key],
                    "u": U[user_key],
                    "p": permission,
                },
            )

        # "readonly": reader/rev/sharer may only read it.
        for who in ("reader", "rev", "sharer"):
            await grant("readonly", who, "read")
        # "hidden": only the reviewer sees it.
        await grant("hidden", "rev", "edit")

    # Second startup: version 1 is backfilled for the rows inserted above.
    await ingest_router._init_db()


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import documents_router
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    files_dir = tmp_path_factory.mktemp("files")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    saved = (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.VERSIONS_DIR,
        ingest_router.DATA_DIR,
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [files_dir.resolve()]
    ingest_router.VERSIONS_DIR = files_dir / "versions"
    ingest_router.DATA_DIR = files_dir / "data"  # export ZIPs
    FILES["dir"] = files_dir
    asyncio.run(_seed(engine, files_dir))
    yield engine
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.VERSIONS_DIR,
        ingest_router.DATA_DIR,
    ) = saved


class _As:
    def __init__(self, client: TestClient, user: CurrentUser | None):
        self._client, self._user = client, user

    def __getattr__(self, method):
        def call(*args, **kwargs):
            api_server.app.dependency_overrides.pop(get_current_user, None)
            if self._user is not None:
                api_server.app.dependency_overrides[get_current_user] = (
                    lambda: self._user
                )
            return getattr(self._client, method)(*args, **kwargs)

        return call


@pytest.fixture()
def as_user(pg, dirs):
    with TestClient(api_server.app, raise_server_exceptions=True) as client:
        yield lambda user: _As(client, user)
    api_server.app.dependency_overrides.clear()


def _q(pg, sql, **params):
    from sqlalchemy import text

    async def run():
        async with pg.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.all() if result.returns_rows else []

    return asyncio.run(run())


def _doc(key, suffix=""):
    return f"/api/db/documents/{T}/{D[key]}{suffix}"


def _downloads(pg, key, user_key):
    return [
        r[0]
        for r in _q(
            pg,
            "SELECT details FROM sdai_audit_log WHERE action = 'document_downloaded'"
            " AND document_id = CAST(:d AS uuid) AND user_id = CAST(:u AS uuid) ORDER BY chain_seq",
            d=D[key],
            u=U[user_key],
        )
    ]


# ── 5.1 Original download ─────────────────────────────────────────────────────


def test_download_current_original(as_user, pg):
    resp = as_user(READER()).get(_doc("pdf", "/download"))
    assert resp.status_code == 200, resp.text
    assert resp.content == open(FILES["pdf"], "rb").read()
    assert resp.headers["content-type"] == "application/pdf"
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="INV-2026-000001_pdf.pdf"' in cd
    [details] = _downloads(pg, "pdf", "reader")
    assert details["via"] == "download" and details["version_no"] == 1
    assert details["file_hash"] == hashlib.sha256(resp.content).hexdigest()


def test_download_rfc5987_name_and_inline(as_user):
    resp = as_user(READER()).get(_doc("png", "/download?inline=true"))
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert cd.startswith("inline;")
    assert 'filename="INV-2026-000002_scan ete.png"' in cd
    assert "filename*=UTF-8''INV-2026-000002_scan%20%C3%A9t%C3%A9.png" in cd


def test_download_given_version(as_user, pg):
    up = as_user(ADMIN()).post(
        f"/api/db/documents/{T}/{D['pdf']}/versions",
        files={"file": ("v2.png", PNG + b"v2", "image/png")},
    )
    assert up.status_code == 200, up.text
    reader = as_user(READER())
    assert reader.get(_doc("pdf", "/download")).content == PNG + b"v2"
    assert (
        reader.get(_doc("pdf", "/download?version=1")).content
        == open(FILES["pdf"], "rb").read()
    )
    assert reader.get(_doc("pdf", "/download?version=9")).status_code == 404
    assert [d["version_no"] for d in _downloads(pg, "pdf", "reader")][-2:] == [2, 1]


def test_download_needs_download_permission(as_user, pg):
    resp = as_user(READER()).get(_doc("readonly", "/download"))
    assert resp.status_code == 403
    assert _downloads(pg, "readonly", "reader") == []


def test_download_hidden_and_trashed_are_404(as_user):
    reader = as_user(READER())
    assert reader.get(_doc("hidden", "/download")).status_code == 404
    assert as_user(ADMIN()).get(_doc("trashed", "/download")).status_code == 404
    assert (
        reader.get(f"/api/db/documents/t_other_x/{D['pdf']}/download").status_code
        == 404
    )


def test_download_clearance_hides_secret(as_user):
    assert as_user(LOWCLEAR()).get(_doc("secret", "/download")).status_code == 404


def test_download_missing_file_404_and_pending_409(as_user, monkeypatch):
    from sync import files as sync_files

    admin = as_user(ADMIN())
    assert admin.get(_doc("pending", "/download")).status_code == 404

    async def pending(conn, path):
        return True

    monkeypatch.setattr(sync_files, "file_pending", pending)
    resp = admin.get(_doc("pending", "/download"))
    assert resp.status_code == 409
    assert resp.headers["X-Sync-Pending"] == "1"


def test_download_watermarks_confidential_pdf(as_user, pg, monkeypatch):
    import document_files
    from pypdf import PdfReader

    original = open(FILES["conf"], "rb").read()
    rev = as_user(REV())
    assert rev.get(_doc("conf", "/download")).content == original  # setting off

    monkeypatch.setattr(document_files, "WATERMARK_CONFIDENTIAL", True)
    resp = rev.get(_doc("conf", "/download"))
    assert resp.status_code == 200 and resp.content != original
    text_ = PdfReader(io.BytesIO(resp.content)).pages[0].extract_text()
    assert "CONFIDENTIAL" in text_ and "rev@example.com" in text_
    assert _downloads(pg, "conf", "rev")[-1]["watermarked"] is True
    # Internal documents and non-PDFs are never stamped.
    assert (
        rev.get(_doc("pdf", "/download?version=1")).content
        == open(FILES["pdf"], "rb").read()
    )
    assert open(FILES["conf"], "rb").read() == original  # stored file untouched


# ── 5.2 Share links ───────────────────────────────────────────────────────────


def _share(client, key, **body):
    return client.post(_doc(key, "/shares"), json=body)


def _token(resp) -> str:
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    assert url.startswith("http://testserver/s/")
    return url.rsplit("/", 1)[1]


def _public(as_user):
    return as_user(None)


def test_share_needs_can_share_and_download(as_user):
    assert _share(as_user(READER()), "png").status_code == 403  # no can_share
    assert _share(as_user(SHARER()), "readonly").status_code == 403  # read only
    assert _share(as_user(SHARER()), "hidden").status_code == 404
    assert _share(as_user(SHARER()), "trashed").status_code == 404


def test_share_confidentiality_rules(as_user):
    assert _share(as_user(ADMIN()), "secret").status_code == 403
    assert _share(as_user(REV()), "conf").status_code == 403
    assert _share(as_user(ADMIN()), "conf").status_code == 200


def test_share_limits_validated(as_user):
    sharer = as_user(SHARER())
    assert _share(sharer, "png", expires_in_days=31).status_code == 422
    assert _share(sharer, "png", max_downloads=0).status_code == 422
    assert _share(sharer, "png", password="abc").status_code == 422
    assert _share(sharer, "png", version_no=9).status_code == 404


def test_token_never_stored_in_clear(as_user, pg):
    token = _token(_share(as_user(SHARER()), "png", password="s3cret!"))
    [(row_json, token_hash, pw_hash)] = _q(
        pg,
        "SELECT to_jsonb(s)::text, token_hash, password_hash FROM sdai_share_links s"
        " WHERE token_hash = :h",
        h=hashlib.sha256(token.encode()).hexdigest(),
    )
    assert token not in row_json and "s3cret!" not in row_json
    assert pw_hash.startswith("$2")  # bcrypt
    for (text_,) in _q(
        pg, "SELECT details::text FROM sdai_audit_log WHERE details IS NOT NULL"
    ):
        assert token not in text_
    for (text_,) in _q(pg, "SELECT row_data::text FROM sdai_sync_changelog"):
        assert token not in text_ and "s3cret!" not in text_


def test_share_landing_and_download(as_user, pg):
    resp = _share(as_user(SHARER()), "png", note="notaire")
    token = _token(resp)
    share = resp.json()["share"]
    assert share["status"] == "active" and share["download_count"] == 0
    assert share["has_password"] is False and share["note"] == "notaire"

    public = _public(as_user)
    page = public.get(f"/s/{token}")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "INV-2026-000002_scan été.png" in page.text
    assert f'action="/s/{token}/download"' in page.text
    assert page.headers["cache-control"] == "no-store"

    dl = public.post(f"/s/{token}/download", headers={"X-Forwarded-For": "203.0.113.9"})
    assert dl.status_code == 200
    assert dl.content == PNG + b"png"
    assert "attachment;" in dl.headers["content-disposition"]

    events = _q(
        pg,
        "SELECT details->>'event', ip_address, user_id FROM sdai_audit_log"
        " WHERE action = 'share_link_accessed' AND details->>'share_id' = :s ORDER BY chain_seq",
        s=share["id"],
    )
    assert [e[0] for e in events] == ["viewed", "downloaded"]
    assert events[-1][1] == "203.0.113.9" and events[-1][2] is None
    listed = as_user(SHARER()).get(_doc("png", "/shares")).json()["shares"]
    assert next(s for s in listed if s["id"] == share["id"])["download_count"] == 1


def test_share_invalid_tokens_are_identical_404s(as_user):
    public = _public(as_user)
    bad = [
        public.get("/s/nope"),
        public.get("/s/" + "A" * 43),
        public.post("/s/" + "A" * 43 + "/download"),
    ]
    assert {r.status_code for r in bad} == {404}
    assert len({r.text for r in bad}) == 1


def test_share_expired(as_user, pg):
    token = _token(_share(as_user(SHARER()), "png"))
    _q(
        pg,
        "UPDATE sdai_share_links SET expires_at = now() - interval '1 minute' WHERE token_hash = :h",
        h=hashlib.sha256(token.encode()).hexdigest(),
    )
    public = _public(as_user)
    assert public.get(f"/s/{token}").status_code == 404
    assert public.post(f"/s/{token}/download").status_code == 404


def test_share_revoked(as_user, pg):
    resp = _share(as_user(SHARER()), "png")
    token, share_id = _token(resp), resp.json()["share"]["id"]
    # Someone else (not an access manager) can neither see nor revoke it.
    assert as_user(REV()).delete(_doc("png", f"/shares/{share_id}")).status_code == 404
    assert all(
        s["id"] != share_id
        for s in as_user(REV()).get(_doc("png", "/shares")).json()["shares"]
    )
    assert (
        as_user(ADMIN()).get(_doc("png", "/shares")).json()["shares"]
    )  # admin sees all
    assert (
        as_user(SHARER()).delete(_doc("png", f"/shares/{share_id}")).status_code == 200
    )
    assert _public(as_user).get(f"/s/{token}").status_code == 404
    [(n,)] = _q(
        pg,
        "SELECT count(*) FROM sdai_audit_log WHERE action = 'share_link_revoked'"
        " AND details->>'share_id' = :s",
        s=share_id,
    )
    assert n == 1


def test_share_password(as_user, pg):
    token = _token(_share(as_user(SHARER()), "png", password="correct horse"))
    public = _public(as_user)
    assert 'type="password"' in public.get(f"/s/{token}").text
    assert public.post(f"/s/{token}/download").status_code == 401
    wrong = public.post(f"/s/{token}/download", data={"password": "nope"})
    assert wrong.status_code == 401 and "Wrong password" in wrong.text
    ok = public.post(f"/s/{token}/download", data={"password": "correct horse"})
    assert ok.status_code == 200 and ok.content == PNG + b"png"
    [(n,)] = _q(
        pg,
        "SELECT count(*) FROM sdai_share_downloads d JOIN sdai_share_links s ON s.id = d.share_id"
        " WHERE s.token_hash = :h",
        h=hashlib.sha256(token.encode()).hexdigest(),
    )
    assert n == 1  # failed attempts never count


def test_share_max_downloads(as_user):
    resp = _share(as_user(SHARER()), "png", max_downloads=2)
    token = _token(resp)
    public = _public(as_user)
    assert "Downloads left" in public.get(f"/s/{token}").text
    assert public.post(f"/s/{token}/download").status_code == 200
    assert public.post(f"/s/{token}/download").status_code == 200
    assert public.post(f"/s/{token}/download").status_code == 404
    assert public.get(f"/s/{token}").status_code == 404
    [s] = [
        s
        for s in as_user(SHARER()).get(_doc("png", "/shares")).json()["shares"]
        if s["id"] == resp.json()["share"]["id"]
    ]
    assert s["status"] == "exhausted" and s["download_count"] == 2


def test_share_dies_with_creators_access(as_user, pg):
    token = _token(_share(as_user(SHARER()), "png"))
    public = _public(as_user)
    assert public.get(f"/s/{token}").status_code == 200
    # Restricting the document to someone else takes it away from the creator.
    _q(
        pg,
        "INSERT INTO sdai_document_access (tenant_id, scope, table_name, document_id,"
        " grantee_type, grantee_id, permission) VALUES (CAST(:t AS uuid), 'document', :tb,"
        " CAST(:d AS uuid), 'user', CAST(:u AS uuid), 'read')",
        t=TENANT["id"],
        tb=T,
        d=D["png"],
        u=U["rev"],
    )
    try:
        assert public.get(f"/s/{token}").status_code == 404
        assert public.post(f"/s/{token}/download").status_code == 404
    finally:
        _q(
            pg,
            "DELETE FROM sdai_document_access WHERE document_id = CAST(:d AS uuid)",
            d=D["png"],
        )
    assert public.get(f"/s/{token}").status_code == 200
    # A deactivated creator's links stop too.
    _q(
        pg,
        "UPDATE sdai_users SET is_active = false WHERE id = CAST(:u AS uuid)",
        u=U["sharer"],
    )
    try:
        assert public.get(f"/s/{token}").status_code == 404
    finally:
        _q(
            pg,
            "UPDATE sdai_users SET is_active = true WHERE id = CAST(:u AS uuid)",
            u=U["sharer"],
        )


def test_share_document_turned_secret_stops_link(as_user, pg):
    token = _token(_share(as_user(SHARER()), "png"))
    _q(
        pg,
        f"UPDATE \"{T}\" SET confidentiality = 'secret' WHERE id = CAST(:d AS uuid)",
        d=D["png"],
    )
    try:
        assert _public(as_user).get(f"/s/{token}").status_code == 404
    finally:
        _q(
            pg,
            f"UPDATE \"{T}\" SET confidentiality = 'internal' WHERE id = CAST(:d AS uuid)",
            d=D["png"],
        )


def test_share_pinned_version_vs_latest(as_user):
    # "pdf" has a version 2 (test_download_given_version).
    sharer = as_user(SHARER())
    latest = _token(_share(sharer, "pdf"))
    pinned = _token(_share(sharer, "pdf", version_no=1))
    public = _public(as_user)
    assert public.post(f"/s/{latest}/download").content == PNG + b"v2"
    assert (
        public.post(f"/s/{pinned}/download").content == open(FILES["pdf"], "rb").read()
    )


def test_share_confidential_is_watermarked(as_user, monkeypatch):
    import document_files
    from pypdf import PdfReader

    monkeypatch.setattr(document_files, "WATERMARK_CONFIDENTIAL", True)
    token = _token(_share(as_user(ADMIN()), "conf"))
    resp = _public(as_user).post(
        f"/s/{token}/download", headers={"Accept-Language": "fr"}
    )
    assert resp.status_code == 200
    text_ = PdfReader(io.BytesIO(resp.content)).pages[0].extract_text()
    assert "CONFIDENTIEL" in text_ and "lien de partage" in text_


def test_share_rows_are_synced_insert_only(pg):
    from sync.capture import SYNC_TABLE_BY_NAME

    assert SYNC_TABLE_BY_NAME["sdai_share_downloads"].insert_only
    rows = _q(
        pg,
        "SELECT DISTINCT table_name, op FROM sdai_sync_changelog"
        " WHERE table_name IN ('sdai_share_links', 'sdai_share_downloads')",
    )
    assert ("sdai_share_links", "insert") in rows and (
        "sdai_share_downloads",
        "insert",
    ) in rows


# ── 5.3 Selective export ──────────────────────────────────────────────────────


def _wait_job(client, job_id, timeout=15.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/db/export/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"export job {job_id} still running")


def _export(client, **body):
    resp = client.post("/api/db/export", json=body)
    assert resp.status_code == 202, resp.text
    job = _wait_job(client, resp.json()["id"])
    assert job["status"] == "done", job
    z = client.get(f"/api/db/export/jobs/{job['id']}/download")
    assert z.status_code == 200, z.text
    assert z.headers["content-type"] == "application/zip"
    import zipfile

    return job, zipfile.ZipFile(io.BytesIO(z.content))


def _refs(*keys):
    return [{"table_name": T, "id": D[k]} for k in keys]


def _check_manifest(zf):
    names = set(zf.namelist())
    lines = zf.read("manifest.sha256").decode().splitlines()
    listed = {}
    for line in lines:
        digest, arc = line.split("  ", 1)
        listed[arc] = digest
        assert hashlib.sha256(zf.read(arc)).hexdigest() == digest, arc
    assert set(listed) == names - {"manifest.sha256"}
    return listed


def test_export_explicit_documents_respects_grants(as_user, pg):
    job, zf = _export(
        as_user(READER()),
        documents=_refs("png", "readonly", "hidden", "secret", "trashed"),
    )
    # readonly: read only; hidden: no grant; trashed: not live - all dropped.
    # secret: the reader's clearance is secret here, and it's downloadable.
    assert job["document_count"] == 2
    meta = json.loads(zf.read("metadata.json"))
    assert {d["id"] for d in meta["documents"]} == {D["png"], D["secret"]}
    blob = b"".join(zf.read(n) for n in zf.namelist())
    for key in ("readonly", "hidden", "trashed"):
        assert D[key].encode() not in blob
    _check_manifest(zf)
    assert {"LISEZMOI.txt", "README.txt", "metadata.csv"} <= set(zf.namelist())
    png = next(d for d in meta["documents"] if d["id"] == D["png"])
    [f] = png["files"]
    assert f["path"] == "files/INV-2026-000002_scan été.png"
    assert zf.read(f["path"]) == PNG + b"png"
    assert f["sha256"] == f["recorded_sha256"] and f["status"] == "ok"
    assert (
        png["record_id"] == "INV-2026-000002"
        and png["fields"]["reference_number"] == "ref png"
    )
    assert "source_image_path" not in json.dumps(meta)  # no server paths
    [(details,)] = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'documents_exported'"
        " AND details->>'job_id' = :j",
        j=job["id"],
    )
    assert {d["id"] for d in details["documents"]} == {D["png"], D["secret"]}
    assert details["zip_sha256"] == job["zip_sha256"]
    [(n,)] = _q(
        pg,
        "SELECT count(*) FROM sdai_audit_log WHERE action = 'export_downloaded'"
        " AND details->>'job_id' = :j",
        j=job["id"],
    )
    assert n == 1


def test_export_nothing_downloadable_is_422(as_user):
    resp = as_user(READER()).post(
        "/api/db/export", json={"documents": _refs("readonly", "hidden")}
    )
    assert resp.status_code == 422


def test_export_selection_must_be_single(as_user):
    reader = as_user(READER())
    assert reader.post("/api/db/export", json={}).status_code == 422
    body = {"documents": _refs("png"), "search": {"q": "x"}}
    assert reader.post("/api/db/export", json=body).status_code == 422


def test_export_all_versions(as_user):
    # "pdf" has versions 1 (original) and 2 (test_download_given_version).
    job, zf = _export(as_user(ADMIN()), documents=_refs("pdf"), versions="all")
    files = sorted(n for n in zf.namelist() if n.startswith("files/"))
    assert files == [
        "files/INV-2026-000001/v1_INV-2026-000001_pdf.pdf",
        "files/INV-2026-000001/v2_INV-2026-000001_v2.png",
    ]
    assert zf.read(files[0]) == open(FILES["pdf"], "rb").read()
    meta = json.loads(zf.read("metadata.json"))
    assert [f["is_current"] for f in meta["documents"][0]["files"]] == [False, True]
    _check_manifest(zf)


def test_export_series_with_subseries(as_user, pg):
    parent, child = _id(), _id()
    _q(
        pg,
        "INSERT INTO sdai_series (id, tenant_id, name, parent_id, path) VALUES"
        " (CAST(:p AS uuid), CAST(:t AS uuid), 'Export parent', NULL, '/' || :p || '/'),"
        " (CAST(:c AS uuid), CAST(:t AS uuid), 'Export child', CAST(:p AS uuid),"
        "  '/' || :p || '/' || :c || '/')",
        p=parent,
        c=child,
        t=TENANT["id"],
    )
    _q(
        pg,
        f'UPDATE "{T}" SET series_id = CAST(:s AS uuid) WHERE id = CAST(:d AS uuid)',
        s=parent,
        d=D["png"],
    )
    _q(
        pg,
        f'UPDATE "{T}" SET series_id = CAST(:s AS uuid) WHERE id = CAST(:d AS uuid)',
        s=child,
        d=D["pdf"],
    )
    _q(
        pg,
        f'UPDATE "{T}" SET series_id = CAST(:s AS uuid) WHERE id = CAST(:d AS uuid)',
        s=child,
        d=D["hidden"],
    )
    reader = as_user(READER())
    job, zf = _export(reader, series_id=parent, include_sub=True)
    meta = json.loads(zf.read("metadata.json"))
    assert {d["id"] for d in meta["documents"]} == {
        D["png"],
        D["pdf"],
    }  # hidden left out
    pdf = next(d for d in meta["documents"] if d["id"] == D["pdf"])
    assert pdf["series"] == "Export parent / Export child"
    job, zf = _export(reader, series_id=parent, include_sub=False)
    assert job["document_count"] == 1


def test_export_search_selection(as_user):
    job, zf = _export(as_user(READER()), search={"q": '"ref png"'})
    meta = json.loads(zf.read("metadata.json"))
    assert [d["id"] for d in meta["documents"]] == [D["png"]]
    assert job["selection"]["kind"] == "search"
    # The reader can read but not download "readonly" - never exported.
    resp = as_user(READER()).post("/api/db/export", json={"search": {"q": "readonly"}})
    assert resp.status_code == 422


def test_export_limits(as_user, monkeypatch):
    import export_router

    reader = as_user(READER())
    monkeypatch.setattr(export_router, "EXPORT_MAX_DOCUMENTS", 1)
    assert (
        reader.post(
            "/api/db/export", json={"documents": _refs("png", "pdf")}
        ).status_code
        == 413
    )
    monkeypatch.setattr(export_router, "EXPORT_MAX_DOCUMENTS", 500)
    monkeypatch.setattr(export_router, "EXPORT_MAX_MB", 0)
    resp = reader.post("/api/db/export", json={"documents": _refs("png")})
    assert resp.status_code == 413 and "MB" in resp.json()["detail"]


def test_export_flags_missing_and_tampered_files(as_user, pg):
    _q(
        pg,
        "UPDATE sdai_document_versions SET file_hash = repeat('0', 64)"
        " WHERE document_id = CAST(:d AS uuid)",
        d=D["secret"],
    )
    job, zf = _export(as_user(ADMIN()), documents=_refs("secret", "pending"))
    meta = json.loads(zf.read("metadata.json"))
    status = {d["id"]: d["files"][0]["status"] for d in meta["documents"]}
    assert status == {D["secret"]: "hash_mismatch", D["pending"]: "missing"}
    assert {w["problem"] for w in job["warnings"]} == {"hash_mismatch", "missing"}
    assert "ATTENTION" in zf.read("LISEZMOI.txt").decode()
    _check_manifest(zf)


def test_export_jobs_are_private_and_expire(as_user, pg):
    import export_router

    job, _ = _export(as_user(READER()), documents=_refs("png"))
    other = as_user(SHARER())
    assert other.get(f"/api/db/export/jobs/{job['id']}").status_code == 404
    assert other.get(f"/api/db/export/jobs/{job['id']}/download").status_code == 404
    assert all(
        j["id"] != job["id"] for j in other.get("/api/db/export/jobs").json()["jobs"]
    )
    assert any(
        j["id"] == job["id"]
        for j in as_user(READER()).get("/api/db/export/jobs").json()["jobs"]
    )

    [(path,)] = _q(
        pg,
        "SELECT file_path FROM sdai_export_jobs WHERE id = CAST(:j AS uuid)",
        j=job["id"],
    )
    assert path.startswith(str(export_router.exports_dir()))
    assert os.path.isfile(path)
    _q(
        pg,
        "UPDATE sdai_export_jobs SET expires_at = now() - interval '1 second' WHERE id = CAST(:j AS uuid)",
        j=job["id"],
    )
    reader = as_user(READER())
    assert reader.get(f"/api/db/export/jobs/{job['id']}/download").status_code == 404
    reader.get("/api/db/export/jobs")  # purges expired ZIPs
    assert not os.path.exists(path)
    assert reader.get(f"/api/db/export/jobs/{job['id']}").json()["status"] == "expired"


def test_export_delete_and_recover(as_user, pg):
    import export_router

    job, _ = _export(as_user(READER()), documents=_refs("png"))
    reader = as_user(READER())
    assert reader.delete(f"/api/db/export/jobs/{job['id']}").status_code == 200
    assert reader.get(f"/api/db/export/jobs/{job['id']}/download").status_code == 404
    # A job a restart interrupted is failed at startup.
    _q(
        pg,
        "INSERT INTO sdai_export_jobs (tenant_id, created_by, status) VALUES"
        " (CAST(:t AS uuid), CAST(:u AS uuid), 'running')",
        t=TENANT["id"],
        u=U["reader"],
    )
    asyncio.run(export_router.recover_jobs())
    rows = _q(
        pg,
        "SELECT status, error FROM sdai_export_jobs WHERE status IN ('running', 'queued')",
    )
    assert rows == []
    assert _q(
        pg, "SELECT count(*) FROM sdai_export_jobs WHERE error = 'interrupted'"
    ) == [(1,)]


def test_export_jobs_never_synced(pg):
    assert _q(
        pg,
        "SELECT count(*) FROM sdai_sync_changelog WHERE table_name = 'sdai_export_jobs'",
    ) == [(0,)]


def test_admin_full_export_unchanged(as_user):
    resp = as_user(ADMIN()).get("/api/admin/export?format=json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert as_user(READER()).get("/api/admin/export").status_code == 403
