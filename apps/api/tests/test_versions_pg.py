"""File versions, field history and the audit hash chain against a real
PostgreSQL (GED Phase 2). Opt-in, like test_access_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_versions_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

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
PDF = b"%PDF-1.4\n"


def _id() -> str:
    return str(uuid.uuid4())


U = {k: _id() for k in ("admin", "editor", "rev", "reader", "lifecycle")}
D = {k: _id() for k in ("pdf", "legacy", "readonly", "locked", "archived", "reex")}
BD = {k: _id() for k in ("pdf", "reex_version")}
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
        can_edit_extraction=flags.get("can_edit_extraction", False),
        group_ids=[],
        locale="en",
        can_manage_lifecycle=flags.get("can_manage_lifecycle", False),
    )


def ADMIN():
    return _user("admin", "admin")


def EDITOR():
    return _user("editor", "reviewer", can_edit_extraction=True)


def REV():
    return _user("rev", "reviewer")


def READER():
    return _user("reader", "reader")


def LIFECYCLE():
    return _user("lifecycle", "reviewer", can_manage_lifecycle=True)


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
        for key, role in [
            ("admin", "admin"),
            ("editor", "reviewer"),
            ("rev", "reviewer"),
            ("reader", "reader"),
            ("lifecycle", "reviewer"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid))"
                ),
                {"id": U[key], "e": f"{key}@example.com", "r": role, "t": TENANT["id"]},
            )
        await ingest_router._ensure_table(
            conn,
            T,
            {
                "document_type": "x",
                "reference_number": "x",
                "amount": "x",
                "items": ["a"],
            },
        )

        batch = (
            await conn.execute(
                text(
                    "INSERT INTO batches (source_type, tenant_id, created_by)"
                    " VALUES ('upload', CAST(:t AS uuid), CAST(:u AS uuid)) RETURNING id"
                ),
                {"t": TENANT["id"], "u": U["editor"]},
            )
        ).scalar()
        TENANT["batch"] = str(batch)

        orig = files_dir / "orig.pdf"
        orig.write_bytes(PDF + b"original")
        FILES["orig"] = str(orig)
        FILES["orig_hash"] = hashlib.sha256(orig.read_bytes()).hexdigest()
        legacy = files_dir / "legacy.png"
        legacy.write_bytes(PNG + b"legacy")
        FILES["legacy"] = str(legacy)
        await conn.execute(
            text(
                "INSERT INTO batch_documents (id, batch_id, filename, source_path)"
                " VALUES (CAST(:id AS uuid), :b, 'orig.pdf', :p)"
            ),
            {"id": BD["pdf"], "b": batch, "p": FILES["orig"]},
        )

        future = datetime.now(timezone.utc) + timedelta(hours=1)

        async def doc(key, **cols):
            base = {
                "id": D[key],
                "source_image_path": FILES["legacy"],
                "document_type": "x",
                "reference_number": f"ref {key}",
                "amount": "10",
                "review_status": "approved",
                "batch_id": TENANT["batch"],
            }
            base.update(cols)
            names = ", ".join(base)
            values = ", ".join(f":{k}" for k in base)
            await conn.execute(
                text(f'INSERT INTO "{T}" ({names}) VALUES ({values})'), base
            )

        await doc(
            "pdf",
            source_pdf_path=FILES["orig"],
            original_path=FILES["orig"],
            original_hash=FILES["orig_hash"],
            content_hash=FILES["orig_hash"],
            batch_document_id=BD["pdf"],
        )
        await doc("legacy")
        await doc("readonly")
        await doc("locked", locked_by=uuid.UUID(U["admin"]), locked_until=future)
        await doc("archived", lifecycle_status="archived")
        await doc("reex", items='["a", "b"]')
        # "readonly": the plain reviewer may only read it.
        await conn.execute(
            text(
                "INSERT INTO sdai_document_access (tenant_id, scope, table_name, document_id,"
                " grantee_type, grantee_id, permission)"
                " VALUES (CAST(:t AS uuid), 'document', :tb, CAST(:d AS uuid), 'user',"
                "         CAST(:u AS uuid), 'read')"
            ),
            {"t": TENANT["id"], "tb": T, "d": D["readonly"], "u": U["rev"]},
        )

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
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [files_dir.resolve()]
    ingest_router.VERSIONS_DIR = files_dir / "versions"
    asyncio.run(_seed(engine, files_dir))
    yield engine
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.VERSIONS_DIR,
    ) = saved


class _As:
    def __init__(self, client: TestClient, user: CurrentUser):
        self._client, self._user = client, user

    def __getattr__(self, method):
        def call(*args, **kwargs):
            api_server.app.dependency_overrides[get_current_user] = lambda: self._user
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
        async with pg.connect() as conn:
            return (await conn.execute(text(sql), params)).all()

    return asyncio.run(run())


def _url(key, suffix=""):
    return f"/api/db/documents/{T}/{D[key]}/versions{suffix}"


def _upload(client, key, content=PNG + b"v2", name="scan v2.png", **form):
    return client.post(
        _url(key), files={"file": (name, content, "image/png")}, data=form
    )


# ── 2.1 Versions ──────────────────────────────────────────────────────────────


def test_backfill_creates_version_one(as_user):
    body = as_user(ADMIN()).get(_url("pdf")).json()
    assert body["current_version"] == 1
    [v1] = body["versions"]
    assert v1["version_no"] == 1 and v1["is_current"]
    assert v1["file_hash"] == FILES["orig_hash"]
    assert v1["mime"] == "application/pdf"
    assert v1["created_by_email"] is None  # no uploaded_by on the seeded row


def test_backfill_legacy_document_has_unhashed_version(as_user):
    [v1] = as_user(ADMIN()).get(_url("legacy")).json()["versions"]
    assert v1["file_hash"] is None  # preprocessed image - existence-only fixity


def test_backfill_is_idempotent(pg):
    import ingest_router

    asyncio.run(ingest_router._init_db())
    [(n,)] = _q(
        pg,
        "SELECT count(*) FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid)",
        d=D["legacy"],
    )
    assert n == 1


def test_upload_new_version(as_user, pg):
    resp = _upload(as_user(EDITOR()), "pdf", comment="rescan")
    assert resp.status_code == 200, resp.text
    v = resp.json()["version"]
    assert v["version_no"] == 2 and v["is_current"] and v["comment"] == "rescan"
    assert v["file_hash"] == hashlib.sha256(PNG + b"v2").hexdigest()
    assert resp.json()["batch_id"] is None
    [(path, cur)] = _q(
        pg,
        f'SELECT v.file_path, d.current_version FROM sdai_document_versions v JOIN "{T}" d'
        " ON d.id = v.document_id WHERE v.document_id = CAST(:d AS uuid) AND v.version_no = 2",
        d=D["pdf"],
    )
    assert cur == 2
    assert path.endswith(f"/default/{D['pdf']}/v2_scan_v2.png")
    assert open(path, "rb").read() == PNG + b"v2"
    [(action, details)] = _q(
        pg,
        "SELECT action, details FROM sdai_audit_log WHERE action = 'document_version_added'"
        " AND document_id = CAST(:d AS uuid)",
        d=D["pdf"],
    )
    assert details["version_no"] == 2 and details["reextract"] is False


def test_version_list_newest_first(as_user):
    body = as_user(READER()).get(_url("pdf")).json()
    assert [v["version_no"] for v in body["versions"]] == [2, 1]
    assert [v["is_current"] for v in body["versions"]] == [True, False]


def test_restore_creates_new_version(as_user, pg):
    resp = as_user(EDITOR()).post(_url("pdf", "/1/restore"), json={"comment": "back"})
    assert resp.status_code == 200, resp.text
    v = resp.json()["version"]
    assert v["version_no"] == 3 and v["restored_from"] == 1
    assert v["file_hash"] == FILES["orig_hash"]
    rows = _q(
        pg,
        "SELECT version_no, file_path FROM sdai_document_versions"
        " WHERE document_id = CAST(:d AS uuid) ORDER BY version_no",
        d=D["pdf"],
    )
    assert [r[0] for r in rows] == [1, 2, 3]
    assert rows[2][1] == rows[0][1] == FILES["orig"]  # same file, history intact


def test_restore_current_version_refused(as_user):
    assert as_user(EDITOR()).post(_url("pdf", "/3/restore")).status_code == 422


def test_restore_unknown_version_404(as_user):
    assert as_user(EDITOR()).post(_url("pdf", "/99/restore")).status_code == 404


def test_download_version(as_user, pg):
    resp = as_user(READER()).get(_url("pdf", "/2/file"))
    assert resp.status_code == 200
    assert resp.content == PNG + b"v2"
    assert "attachment" in resp.headers["content-disposition"]
    rows = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'document_downloaded'"
        " AND document_id = CAST(:d AS uuid) AND user_id = CAST(:u AS uuid)",
        d=D["pdf"],
        u=U["reader"],
    )
    assert rows and rows[-1][0] == {"version_no": 2, "via": "version"}


def test_reader_cannot_upload_or_restore(as_user):
    reader = as_user(READER())
    assert _upload(reader, "pdf").status_code == 403
    assert reader.post(_url("pdf", "/1/restore")).status_code == 403


def test_upload_needs_edit_permission(as_user):
    rev = as_user(REV())
    assert rev.get(_url("readonly")).status_code == 200  # can read...
    assert _upload(rev, "readonly").status_code == 403  # ...not edit
    assert rev.post(_url("readonly", "/1/restore")).status_code == 403


def test_upload_rejects_bad_content(as_user):
    editor = as_user(EDITOR())
    assert _upload(editor, "legacy", content=b"not a png").status_code == 422
    assert _upload(editor, "legacy", name="x.exe").status_code == 422


def test_locked_by_someone_else_refused(as_user):
    assert _upload(as_user(EDITOR()), "locked").status_code == 409
    # The lock holder itself may.
    assert _upload(as_user(ADMIN()), "locked").status_code == 200


def test_archived_needs_lifecycle_manager(as_user):
    assert _upload(as_user(EDITOR()), "archived").status_code == 403
    assert _upload(as_user(ADMIN()), "archived").status_code == 200
    assert as_user(LIFECYCLE()).post(_url("archived", "/1/restore")).status_code == 200


def test_unknown_document_404(as_user):
    url = f"/api/db/documents/{T}/{_id()}/versions"
    assert as_user(ADMIN()).get(url).status_code == 404
    assert (
        as_user(ADMIN())
        .post(url, files={"file": ("a.png", PNG, "image/png")})
        .status_code
        == 404
    )


def test_upload_with_reextract_queues_target(as_user, pg):
    import ingest_router

    while not ingest_router._queue.empty():
        ingest_router._queue.get_nowait()
    resp = _upload(as_user(EDITOR()), "reex", reextract="true")
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]
    assert batch_id
    item = ingest_router._queue.get_nowait()
    assert item[1] == batch_id
    [(target_table, target_doc, target_version, source_type)] = _q(
        pg,
        "SELECT bd.target_table, bd.target_document_id::text, bd.target_version, b.source_type"
        " FROM batch_documents bd JOIN batches b ON b.id = bd.batch_id WHERE bd.id = CAST(:bd AS uuid)",
        bd=item[0],
    )
    assert (target_table, target_doc, target_version, source_type) == (
        T,
        D["reex"],
        2,
        "version",
    )


def test_reextraction_updates_same_row_and_records_history(as_user, pg):
    import ingest_router

    [(bd_id,)] = _q(
        pg,
        "SELECT id::text FROM batch_documents WHERE target_document_id = CAST(:d AS uuid)",
        d=D["reex"],
    )
    target = asyncio.run(ingest_router._reextract_target(bd_id))
    assert target == (T, D["reex"], 2)
    applied = asyncio.run(
        ingest_router._apply_reextraction(
            bd_id,
            target,
            {
                "document_type": "contract",  # never moves the row
                "reference_number": "ref reex v2",
                "amount": "10",  # unchanged - no history row
                "items": ["a", "b", "c"],
                "new_field": "hello",
                "extraction_confidence": "medium",
            },
            confidence="medium",
            review_status="review_required",
            primary_image="/p/1.png",
            page_image_paths=["/p/1.png"],
            source_pdf_path=None,
        )
    )
    assert applied["fields_changed"] == ["items", "new_field", "reference_number"]
    [(ref, doc_type, status, pages, new_field)] = _q(
        pg,
        f'SELECT reference_number, document_type, review_status, page_image_paths, new_field FROM "{T}"'
        " WHERE id = CAST(:d AS uuid)",
        d=D["reex"],
    )
    assert (ref, doc_type, status, pages, new_field) == (
        "ref reex v2",
        "x",
        "review_required",
        ["/p/1.png"],
        "hello",
    )
    rows = _q(
        pg,
        "SELECT field, old_value, new_value, change_source, version_no, changed_by::text"
        " FROM sdai_field_history WHERE document_id = CAST(:d AS uuid) ORDER BY field",
        d=D["reex"],
    )
    assert rows == [
        ("items", ["a", "b"], ["a", "b", "c"], "reextract", 2, U["editor"]),
        ("new_field", None, "hello", "reextract", 2, U["editor"]),
        ("reference_number", "ref reex", "ref reex v2", "reextract", 2, U["editor"]),
    ]
    [(details,)] = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'document_reextracted'"
        " AND document_id = CAST(:d AS uuid)",
        d=D["reex"],
    )
    assert details["detected_type"] == "contract"


def test_reextraction_upload_follows_target_document(pg):
    """A version's upload (its pages, page images) follows the access rules
    of the document it re-extracts into - here one the reviewer may only
    read."""
    import documents_router
    from sqlalchemy import text

    bd = _id()

    async def run():
        async with pg.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO batch_documents (id, batch_id, filename, target_table,"
                    " target_document_id, target_version)"
                    " VALUES (CAST(:id AS uuid), CAST(:b AS uuid), 'v.png', :t, CAST(:d AS uuid), 2)"
                ),
                {"id": bd, "b": TENANT["batch"], "t": T, "d": D["readonly"]},
            )
        check = documents_router._filed_document_permitted
        return (
            await check([bd], REV(), "read"),
            await check([bd], REV(), "edit"),
            await check([bd], EDITOR(), "edit"),
        )

    assert asyncio.run(run()) == (True, False, False)


# ── 2.2 Field history ─────────────────────────────────────────────────────────


def _history(pg, key):
    return _q(
        pg,
        "SELECT field, old_value, new_value, change_source, changed_by::text"
        " FROM sdai_field_history WHERE document_id = CAST(:d AS uuid) ORDER BY created_at, field",
        d=D[key],
    )


def test_field_edit_records_history_in_same_transaction(as_user, pg):
    resp = as_user(EDITOR()).patch(
        f"/api/db/documents/{T}/{D['legacy']}/fields",
        json={"fields": {"reference_number": "ref legacy fixed", "amount": "10"}},
    )
    assert resp.status_code == 200, resp.text
    # amount was submitted unchanged - only the real change is recorded.
    assert _history(pg, "legacy") == [
        ("reference_number", "ref legacy", "ref legacy fixed", "edit", U["editor"])
    ]
    [(details,)] = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'document_fields_edited'"
        " AND document_id = CAST(:d AS uuid)",
        d=D["legacy"],
    )
    assert details["changes"] == {
        "reference_number": {"old": "ref legacy", "new": "ref legacy fixed"}
    }
    assert sorted(details["fields_changed"]) == ["amount", "reference_number"]


def test_clearing_a_field_is_recorded(as_user, pg):
    as_user(EDITOR()).patch(
        f"/api/db/documents/{T}/{D['legacy']}/fields", json={"fields": {"amount": ""}}
    )
    assert _history(pg, "legacy")[-1] == ("amount", "10", None, "edit", U["editor"])


def test_failed_edit_writes_no_history(as_user, pg):
    before = len(_history(pg, "readonly"))
    resp = as_user(REV()).patch(
        f"/api/db/documents/{T}/{D['readonly']}/fields",
        json={"fields": {"amount": "99"}},
    )
    assert resp.status_code == 403
    assert len(_history(pg, "readonly")) == before


def test_review_approve_with_edits_records_history(as_user, pg):
    resp = as_user(EDITOR()).patch(
        f"/api/review/{T}/{D['archived']}",
        json={"action": "approve", "fields": {"amount": "20", "items": "x, y"}},
    )
    assert resp.status_code == 200, resp.text
    rows = _history(pg, "archived")
    assert ("amount", "10", "20", "review", U["editor"]) in rows
    assert ("items", None, ["x", "y"], "review", U["editor"]) in rows


def test_review_approve_without_edits_records_nothing(as_user, pg):
    before = len(_history(pg, "pdf"))
    resp = as_user(EDITOR()).patch(
        f"/api/review/{T}/{D['pdf']}", json={"action": "approve", "fields": {}}
    )
    assert resp.status_code == 200
    assert len(_history(pg, "pdf")) == before


def test_page_remerge_of_filed_document_records_history(pg):
    """A page retried or entered by hand after the document was filed
    re-merges into it - that change is recorded too."""
    import ingest_router

    _exec(
        pg,
        "UPDATE batch_documents SET status = 'completed' WHERE id = CAST(:bd AS uuid)",
        bd=BD["pdf"],
    )
    _exec(
        pg,
        "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path, status, fields)"
        " VALUES (CAST(:bd AS uuid), 1, :img, 'completed', CAST(:f AS jsonb))",
        bd=BD["pdf"],
        img=FILES["legacy"],
        f='{"document_type": "invoice", "reference_number": "ref pdf merged", "extraction_confidence": "medium"}',
    )
    asyncio.run(ingest_router._merge_page_into_document(BD["pdf"]))
    assert (
        "reference_number",
        "ref pdf",
        "ref pdf merged",
        "reextract",
        None,
    ) in _history(pg, "pdf")


def test_history_endpoint(as_user):
    body = as_user(READER()).get(f"/api/db/documents/{T}/{D['legacy']}/history").json()
    assert [f["field"] for f in body["fields"]] == [
        "amount",
        "reference_number",
    ]  # newest first
    assert body["fields"][1]["changed_by_email"] == "editor@example.com"
    actions = {e["action"] for e in body["events"]}
    assert "document_fields_edited" in actions
    assert all("ip_address" not in e for e in body["events"])


def test_history_hides_access_tracking_from_non_managers(as_user):
    as_user(READER()).get(_url("pdf", "/1/file"))  # a download event
    url = f"/api/db/documents/{T}/{D['pdf']}/history"
    reader_actions = {e["action"] for e in as_user(READER()).get(url).json()["events"]}
    admin_actions = {e["action"] for e in as_user(ADMIN()).get(url).json()["events"]}
    assert "document_downloaded" not in reader_actions
    assert "document_downloaded" in admin_actions
    assert "document_version_added" in reader_actions


def test_history_needs_read(as_user):
    url = f"/api/db/documents/{T}/{D['readonly']}/history"
    assert (
        as_user(READER()).get(url).status_code == 404
    )  # restricted, reader has no grant


# ── 2.3 Access audit ──────────────────────────────────────────────────────────


def _events(pg, action, key, user):
    return _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = :a AND document_id = CAST(:d AS uuid)"
        " AND user_id = CAST(:u AS uuid)",
        a=action,
        d=D[key],
        u=U[user],
    )


def test_document_viewed_once_per_window(as_user, pg):
    import cache

    cache._local_windows.clear()
    lifecycle = as_user(LIFECYCLE())
    for _ in range(3):
        assert lifecycle.get(f"/api/db/documents/{T}/{D['legacy']}").status_code == 200
    assert len(_events(pg, "document_viewed", "legacy", "lifecycle")) == 1


def test_original_served_through_image_is_a_download(as_user, pg):
    import cache

    cache._local_windows.clear()
    resp = as_user(LIFECYCLE()).get(
        "/api/db/image", params={"path": FILES["orig"], "table": T, "id": D["pdf"]}
    )
    assert resp.status_code == 200
    [(details,)] = _events(pg, "document_downloaded", "pdf", "lifecycle")
    assert details == {"via": "image", "file": "orig.pdf"}


def test_page_image_is_not_a_download(as_user, pg, monkeypatch):
    import cache
    import documents_router

    cache._local_windows.clear()
    # Treat the files directory as the page-image directory.
    monkeypatch.setattr(
        documents_router, "_IMAGES_ROOT", documents_router._SAFE_FILE_ROOTS[0]
    )
    resp = as_user(LIFECYCLE()).get(
        "/api/db/image", params={"path": FILES["legacy"], "table": T, "id": D["legacy"]}
    )
    assert resp.status_code == 200
    assert _events(pg, "document_downloaded", "legacy", "lifecycle") == []


# ── 2.4 Tamper-evident audit chain + fixity on versions ──────────────────────


def _exec(pg, sql, *, replica=False, **params):
    """Run one statement in its own transaction - with replica=True the
    triggers are bypassed, i.e. what someone with direct DB access could do."""
    from sqlalchemy import text

    async def run():
        async with pg.begin() as conn:
            if replica:
                await conn.execute(text("SET LOCAL session_replication_role = replica"))
            return await conn.execute(text(sql), params)

    return asyncio.run(run())


def _verify():
    import ingest_router

    return asyncio.run(ingest_router._verify_audit_chain())


def test_every_entry_is_chained(pg):
    rows = _q(
        pg,
        "SELECT origin_node, chain_seq, prev_hash, row_hash FROM sdai_audit_log ORDER BY chain_seq",
    )
    assert rows, "earlier tests logged entries"
    assert {r[0] for r in rows} == {"local"}
    assert [r[1] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0][2] is None
    assert all(rows[i][2] == rows[i - 1][3] for i in range(1, len(rows)))
    result = _verify()
    assert result["ok"] and result["checked"] == len(rows)


def test_concurrent_log_action_never_forks_the_chain(pg):
    from audit import log_action

    async def burst():
        await asyncio.gather(
            *(log_action(action="chain_test", details={"i": i}) for i in range(25))
        )

    asyncio.run(burst())
    [(n,)] = _q(pg, "SELECT count(*) FROM sdai_audit_log WHERE action = 'chain_test'")
    assert n == 25
    assert _verify()["ok"]


def test_details_with_awkward_json_still_verify(pg):
    from audit import log_action

    asyncio.run(
        log_action(
            action="chain_test",
            details={
                "f": 1e20,
                "g": 0.1,
                "nested": {"b": [1, "é", None], "a": True},
                "s": "quote\"'",
            },
        )
    )
    assert _verify()["ok"]


def test_audit_log_is_append_only(pg):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"):
        _exec(pg, "UPDATE sdai_audit_log SET action = 'forged' WHERE chain_seq = 1")
    with pytest.raises(DBAPIError, match="append-only"):
        _exec(pg, "DELETE FROM sdai_audit_log WHERE chain_seq = 1")


def test_edited_row_is_detected(pg):
    [(row_id, email)] = _q(
        pg, "SELECT id::text, user_email FROM sdai_audit_log WHERE chain_seq = 3"
    )
    _exec(
        pg,
        "UPDATE sdai_audit_log SET user_email = 'someone-else@example.com' WHERE id = CAST(:id AS uuid)",
        replica=True,
        id=row_id,
    )
    try:
        result = _verify()
        assert not result["ok"]
        assert result["first_broken"] == {
            "reason": "row_altered",
            "origin_node": "local",
            "chain_seq": 3,
            "id": row_id,
        }
    finally:
        _exec(
            pg,
            "UPDATE sdai_audit_log SET user_email = :e WHERE id = CAST(:id AS uuid)",
            replica=True,
            e=email,
            id=row_id,
        )
    assert _verify()["ok"]


def _snapshot(pg, seq):
    return _q(
        pg,
        "SELECT id, user_id, user_email, action, table_name, document_id, CAST(details AS text),"
        " ip_address, created_at, origin_node, chain_seq, prev_hash, row_hash"
        " FROM sdai_audit_log WHERE chain_seq = :s",
        s=seq,
    )[0]


def _reinsert(pg, row):
    _exec(
        pg,
        "INSERT INTO sdai_audit_log (id, user_id, user_email, action, table_name, document_id, details,"
        " ip_address, created_at, origin_node, chain_seq, prev_hash, row_hash)"
        " VALUES (:id, :uid, :email, :action, :t, :doc, CAST(:details AS jsonb), :ip, :at, :node, :seq,"
        " :prev, :hash)",
        replica=True,
        id=row[0],
        uid=row[1],
        email=row[2],
        action=row[3],
        t=row[4],
        doc=row[5],
        details=row[6],
        ip=row[7],
        at=row[8],
        node=row[9],
        seq=row[10],
        prev=row[11],
        hash=row[12],
    )


def test_deleted_row_is_detected(pg):
    row = _snapshot(pg, 2)
    _exec(pg, "DELETE FROM sdai_audit_log WHERE chain_seq = 2", replica=True)
    try:
        result = _verify()
        assert result["first_broken"]["reason"] == "missing_rows"
        assert result["first_broken"]["chain_seq"] == 2
    finally:
        _reinsert(pg, row)
    assert _verify()["ok"]


def test_removed_tail_is_detected(pg):
    assert _verify()["ok"]  # records the head
    [(last,)] = _q(pg, "SELECT max(chain_seq) FROM sdai_audit_log")
    row = _snapshot(pg, last)
    _exec(pg, "DELETE FROM sdai_audit_log WHERE chain_seq = :s", replica=True, s=last)
    try:
        result = _verify()
        assert result["first_broken"]["reason"] == "truncated"
        assert result["first_broken"]["chain_seq"] == last
    finally:
        _reinsert(pg, row)
    assert _verify()["ok"]


def test_legacy_rows_are_chained_at_startup(pg):
    import ingest_router

    _exec(
        pg,
        "INSERT INTO sdai_audit_log (action, details, created_at) VALUES"
        " ('legacy_a', '{\"x\": 1}', now() - interval '2 days'),"
        " ('legacy_b', NULL, now() - interval '1 day')",
        replica=True,
    )
    assert _verify()["first_broken"]["reason"] == "unchained"
    asyncio.run(ingest_router._init_db())
    rows = _q(
        pg,
        "SELECT action FROM sdai_audit_log WHERE action LIKE 'legacy_%' ORDER BY chain_seq",
    )
    assert [r[0] for r in rows] == [
        "legacy_a",
        "legacy_b",
    ]  # oldest first, after the chain's tail
    assert _verify()["ok"]


def test_integrity_endpoints(as_user, pg):
    admin = as_user(ADMIN())
    chain = admin.get("/api/admin/integrity/audit-chain").json()
    assert chain["ok"] and chain["chains"][0]["origin_node"] == "local"

    body = admin.post("/api/admin/integrity-check").json()
    assert body["audit_chain"]["ok"] is True
    assert (
        body["versions_checked"] >= 8
        and body["versions_ok"] == body["versions_checked"]
    )

    last = admin.get("/api/admin/integrity").json()["last_run"]
    assert last["ran_by"] == "admin@example.com"
    assert last["fixity"]["versions_checked"] == body["versions_checked"]
    assert last["audit_chain"]["ok"] is True


def test_fixity_covers_version_files(as_user, pg):
    [(path,)] = _q(
        pg,
        "SELECT file_path FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid) AND version_no = 2",
        d=D["pdf"],
    )
    original = open(path, "rb").read()
    with open(path, "ab") as fh:
        fh.write(b"bit rot")
    try:
        body = as_user(ADMIN()).post("/api/admin/integrity-check").json()
        assert (
            body["mismatched"] == 1
            and body["versions_ok"] == body["versions_checked"] - 1
        )
        details = [
            r[0]
            for r in _q(
                pg,
                "SELECT details FROM sdai_audit_log WHERE action = 'integrity_check_failed'"
                " AND document_id = CAST(:d AS uuid)",
                d=D["pdf"],
            )
        ]
        assert {"reasons": ["version hash mismatch"], "version_no": 2} in details
    finally:
        open(path, "wb").write(original)


def test_broken_chain_is_logged(pg):
    import ingest_router

    row = _snapshot(pg, 5)
    _exec(
        pg,
        "UPDATE sdai_audit_log SET action = 'forged' WHERE chain_seq = 5",
        replica=True,
    )
    try:
        chain = asyncio.run(ingest_router._audit_chain_check())
        assert not chain["ok"]
    finally:
        _exec(
            pg,
            "UPDATE sdai_audit_log SET action = :a WHERE chain_seq = 5",
            replica=True,
            a=row[3],
        )
    [(details,)] = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'integrity_chain_broken' ORDER BY chain_seq DESC LIMIT 1",
    )
    assert details["reason"] == "row_altered" and details["chain_seq"] == 5
    assert _verify()["ok"]
