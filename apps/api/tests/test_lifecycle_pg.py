"""Lifecycle, trash, retention, legal hold and destruction against a real
PostgreSQL (GED Phase 6). Opt-in, like test_access_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_lifecycle_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import hashlib
import io
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

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
T2 = "t_default_letter"


def _id() -> str:
    return str(uuid.uuid4())


U = {k: _id() for k in ("admin", "admin2", "archivist", "editor", "rev", "reader")}
TENANT: dict = {}
ENV: dict = {}


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
        clearance="secret",
    )


def ADMIN():
    return _user("admin", "admin")


def ADMIN2():
    return _user("admin2", "admin")


def ARCHIVIST():
    return _user(
        "archivist", "reviewer", can_manage_lifecycle=True, can_edit_extraction=True
    )


def EDITOR():
    return _user("editor", "reviewer", can_edit_extraction=True)


def REV():
    return _user("rev", "reviewer")


_ROLES = {
    "admin": ("admin", False),
    "admin2": ("admin", False),
    "archivist": ("reviewer", True),
    "editor": ("reviewer", False),
    "rev": ("reviewer", False),
    "reader": ("reader", False),
}


async def _seed(engine):
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
        for key, (role, lifecycle_flag) in _ROLES.items():
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id,"
                    " can_manage_lifecycle, can_edit_extraction, clearance)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid), :l, :x, 'secret')"
                ),
                {
                    "id": U[key],
                    "e": f"{key}@example.com",
                    "r": role,
                    "t": TENANT["id"],
                    "l": lifecycle_flag,
                    "x": key in ("archivist", "editor"),
                },
            )
        for table in (T, T2):
            await ingest_router._ensure_table(
                conn,
                table,
                {
                    "document_type": "x",
                    "reference_number": "x",
                    "amount": "x",
                    "date_acte": "x",
                },
            )


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import destruction
    import documents_router
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sync import config as sync_config

    files_dir = tmp_path_factory.mktemp("files").resolve()
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    saved = (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        destruction.DATA_DIR,
        sync_config.file_roots,
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [files_dir]
    destruction.DATA_DIR = files_dir / "data"
    sync_config.file_roots = lambda: [files_dir]
    ENV["dir"] = files_dir
    asyncio.run(_seed(engine))
    yield engine
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        destruction.DATA_DIR,
        sync_config.file_roots,
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


def _run(coro_fn):
    return asyncio.run(coro_fn())


_counter = [0]


def _mk(pg, table=T, *, pages=1, with_upload=True, **cols) -> str:
    """A filed document with its own files: original, a version row, an
    upload with page rows and page images."""
    import ingest_router
    from sqlalchemy import text

    _counter[0] += 1
    n = _counter[0]
    doc_id = _id()
    d = ENV["dir"] / f"doc{n}"
    d.mkdir()
    original = d / "original.pdf"
    original.write_bytes(b"%PDF-1.4 original " + doc_id.encode())
    images = []
    for p in range(pages):
        img = d / f"page{p + 1}.png"
        img.write_bytes(b"\x89PNG page " + doc_id.encode() + bytes([p]))
        images.append(str(img))
    content_hash = cols.pop(
        "content_hash", hashlib.sha256(original.read_bytes()).hexdigest()
    )

    async def run():
        async with pg.begin() as conn:
            bd = None
            if with_upload:
                batch = (
                    await conn.execute(
                        text(
                            "INSERT INTO batches (source_type, tenant_id) VALUES ('upload',"
                            " CAST(:t AS uuid)) RETURNING id::text"
                        ),
                        {"t": TENANT["id"]},
                    )
                ).scalar()
                bd = (
                    await conn.execute(
                        text(
                            "INSERT INTO batch_documents (batch_id, filename, status, source_path,"
                            " image_path) VALUES (CAST(:b AS uuid), 'original.pdf', 'completed',"
                            " :sp, :ip) RETURNING id::text"
                        ),
                        {"b": batch, "sp": str(original), "ip": images[0]},
                    )
                ).scalar()
                for p, img in enumerate(images, 1):
                    await conn.execute(
                        text(
                            "INSERT INTO batch_document_pages (batch_document_id, page_number,"
                            " image_path, status, ocr_text) VALUES (CAST(:bd AS uuid), :n, :p,"
                            " 'completed', 'texte de la page')"
                        ),
                        {"bd": bd, "n": p, "p": img},
                    )
            base = {
                "id": doc_id,
                "source_image_path": images[0],
                "page_image_paths": json.dumps(images),
                "document_type": "invoice",
                "reference_number": f"REF-{n}",
                "amount": "10",
                "review_status": "approved",
                "original_path": str(original),
                "original_hash": content_hash,
                "content_hash": content_hash,
                "record_id": f"INV-2026-{n:06d}",
                "batch_document_id": bd,
            }
            base.update(cols)
            names = ", ".join(base)
            values = ", ".join(
                f"CAST(:{k} AS jsonb)" if k == "page_image_paths" else f":{k}"
                for k in base
            )
            await conn.execute(
                text(f'INSERT INTO "{table}" ({names}) VALUES ({values})'), base
            )
            await ingest_router._insert_first_version(
                conn,
                TENANT["id"],
                table,
                "id = CAST(:__doc AS uuid)",
                {"__doc": doc_id},
            )

    asyncio.run(run())
    ENV[doc_id] = {"original": str(original), "images": images, "dir": d}
    return doc_id


def _doc(doc_id, suffix="", table=T):
    return f"/api/db/documents/{table}/{doc_id}{suffix}"


def _row(pg, doc_id, cols="*", table=T):
    rows = _q(
        pg, f'SELECT {cols} FROM "{table}" WHERE id = CAST(:id AS uuid)', id=doc_id
    )
    return rows[0] if rows else None


def _audit(pg, action, doc_id=None):
    if doc_id:
        return _q(
            pg,
            "SELECT details FROM sdai_audit_log WHERE action = :a AND document_id = CAST(:d AS uuid)",
            a=action,
            d=doc_id,
        )
    return _q(pg, "SELECT details FROM sdai_audit_log WHERE action = :a", a=action)


def _series(pg, name, parent=None) -> str:
    return _q(
        pg,
        "INSERT INTO sdai_series (tenant_id, name, parent_id) VALUES (CAST(:t AS uuid), :n,"
        " CAST(:p AS uuid)) RETURNING id::text",
        t=TENANT["id"],
        n=name,
        p=parent,
    )[0][0]


def _set_status(pg, doc_id, status, table=T, **extra):
    """Test setup only - production code goes through lifecycle.py."""
    sets = ", ".join(
        [f"lifecycle_status = '{status}'"] + [f"{k} = :{k}" for k in extra]
    )
    _q(
        pg,
        f'UPDATE "{table}" SET {sets} WHERE id = CAST(:id AS uuid)',
        id=doc_id,
        **extra,
    )


# ── 6.1 State machine ─────────────────────────────────────────────────────────


def test_transition_table():
    import lifecycle

    lifecycle.check_transition("active", "archived", "archive")
    lifecycle.check_transition("archived", "destruction_eligible", "retention")
    lifecycle.check_transition("destruction_eligible", "archived", "extend")
    lifecycle.check_transition("destruction_eligible", "destroyed", "destruction")
    for frm in ("active", "archived", "destruction_eligible"):
        lifecycle.check_transition(frm, "destroyed", "purge")
    for frm, to, op in (
        ("active", "destruction_eligible", "retention"),
        ("active", "destroyed", "destruction"),
        ("archived", "destroyed", "destruction"),
        ("archived", "destruction_eligible", "archive"),
        ("archived", "active", "archive"),
        ("destroyed", "archived", "extend"),
        ("destroyed", "destroyed", "purge"),
    ):
        with pytest.raises(lifecycle.LifecycleError) as exc:
            lifecycle.check_transition(frm, to, op)
        assert exc.value.status == 409


def test_archive_needs_lifecycle_manager_and_is_one_way(as_user, pg):
    doc = _mk(pg)
    assert as_user(EDITOR()).post(_doc(doc, "/archive")).status_code == 403
    assert as_user(ARCHIVIST()).post(_doc(doc, "/archive")).status_code == 200
    assert _row(pg, doc, "lifecycle_status, archived_by::text")[0:2] == (
        "archived",
        U["archivist"],
    )
    again = as_user(ARCHIVIST()).post(_doc(doc, "/archive"))
    assert again.status_code == 409
    assert _audit(pg, "document_archived", doc)


def test_archived_document_is_read_only_except_for_lifecycle_managers(as_user, pg):
    doc = _mk(pg)
    _set_status(pg, doc, "archived")
    edit = {"fields": {"amount": "99"}}
    resp = as_user(EDITOR()).patch(_doc(doc, "/fields"), json=edit)
    assert resp.status_code == 403
    assert "archived" in resp.json()["detail"]
    assert (
        as_user(EDITOR()).post(_doc(doc, "/tags"), json={"name": "x"}).status_code
        == 403
    )
    assert (
        as_user(EDITOR())
        .patch(f"/api/review/{T}/{doc}", json={"action": "reject"})
        .status_code
        == 403
    )
    # Readable, and the UI is told the caller can't edit.
    assert as_user(EDITOR()).get(_doc(doc)).status_code == 200
    assert (
        as_user(EDITOR()).get(_doc(doc, "/access")).json()["my_permission"]
        == "download"
    )
    assert (
        as_user(ARCHIVIST()).get(_doc(doc, "/access")).json()["my_permission"] == "edit"
    )
    assert (
        as_user(ARCHIVIST()).patch(_doc(doc, "/fields"), json=edit).status_code == 200
    )
    _set_status(pg, doc, "destruction_eligible")
    assert as_user(EDITOR()).patch(_doc(doc, "/fields"), json=edit).status_code == 403


def test_review_queue_skips_archived_for_non_managers(as_user, pg):
    doc = _mk(pg, review_status="review_required")
    _set_status(pg, doc, "archived")
    ids = [r["id"] for r in as_user(EDITOR()).get("/api/review/queue").json()["items"]]
    assert doc not in ids


# ── 6.2 Trash ─────────────────────────────────────────────────────────────────


def test_trash_hides_and_restore_brings_back(as_user, pg):
    doc, other = _mk(pg), _mk(pg)
    link = as_user(ADMIN()).post(
        _doc(other, "/links"),
        json={"to_table": T, "to_id": doc, "relation": "concerne"},
    )
    assert link.status_code in (200, 201), link.text
    assert as_user(EDITOR()).post(_doc(doc, "/trash")).status_code == 403
    resp = as_user(ARCHIVIST()).post(_doc(doc, "/trash"), json={"reason": "doublon"})
    assert resp.status_code == 200, resp.text
    assert _row(pg, doc, "deleted_by::text, deleted_reason") == (
        U["archivist"],
        "doublon",
    )
    # Gone from every user-facing view, links hidden (not deleted).
    assert as_user(ADMIN()).get(_doc(doc)).status_code == 404
    listed = as_user(ADMIN()).get(
        "/api/db/documents", params={"q": f"REF-{_counter[0] - 1}"}
    )
    assert doc not in [r["id"] for r in listed.json()["results"]]
    assert as_user(ADMIN()).get(_doc(other, "/links")).json()["links"] == []
    assert (
        _q(
            pg,
            "SELECT count(*) FROM sdai_document_links WHERE to_id = CAST(:d AS uuid)",
            d=doc,
        )[0][0]
        == 1
    )
    trash = as_user(ARCHIVIST()).get("/api/db/trash").json()
    [item] = [i for i in trash["items"] if i["id"] == doc]
    assert item["deleted_by"] == "archivist@example.com" and item["restorable"]
    assert item["purge_at"].startswith(
        (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    )
    assert as_user(EDITOR()).get("/api/db/trash").status_code == 403
    # Restore.
    assert as_user(ARCHIVIST()).post(_doc(doc, "/restore")).status_code == 200
    assert as_user(ADMIN()).get(_doc(doc)).status_code == 200
    assert len(as_user(ADMIN()).get(_doc(other, "/links")).json()["links"]) == 1
    assert _audit(pg, "document_trashed", doc)[0][0]["reason"] == "doublon"
    assert _audit(pg, "document_restored", doc)
    assert as_user(ARCHIVIST()).post(_doc(doc, "/restore")).status_code == 409


def test_old_delete_routes_now_trash(as_user, pg):
    doc = _mk(pg)
    assert as_user(ADMIN()).delete(f"/api/review/{T}/{doc}").status_code == 200
    assert _row(pg, doc, "deleted_at IS NOT NULL")[0] is True
    bd = _row(pg, (doc2 := _mk(pg)), "batch_document_id::text")[0]
    assert as_user(ADMIN()).delete(f"/api/ingest/documents/{bd}").status_code == 200
    assert _row(pg, doc2, "deleted_at IS NOT NULL")[0] is True
    assert (
        _q(
            pg, "SELECT count(*) FROM batch_documents WHERE id = CAST(:b AS uuid)", b=bd
        )[0][0]
        == 1
    )
    # A batch with a filed document is hidden, not deleted.
    doc3 = _mk(pg)
    batch = _q(
        pg,
        f'SELECT bd.batch_id::text FROM "{T}" d JOIN batch_documents bd ON bd.id = d.batch_document_id'
        " WHERE d.id = CAST(:d AS uuid)",
        d=doc3,
    )[0][0]
    assert as_user(ADMIN()).delete(f"/api/ingest/batches/{batch}").status_code == 200
    assert _row(pg, doc3, "deleted_at IS NOT NULL")[0] is True
    assert _q(
        pg,
        "SELECT removed_at IS NOT NULL FROM batches WHERE id = CAST(:b AS uuid)",
        b=batch,
    ) == [(True,)]
    listed = as_user(ADMIN()).get("/api/ingest/batches").json()
    assert batch not in [b["batch_id"] for b in listed]


def test_dedup_still_works_after_trash(as_user, pg):
    import ingest_router

    doc = _mk(pg)
    h = _row(pg, doc, "content_hash")[0]
    assert asyncio.run(ingest_router._find_duplicate(h, "default"))["id"] == doc
    as_user(ARCHIVIST()).post(_doc(doc, "/trash"))
    # The same file can be filed again (partial unique index, live-only dedup).
    assert asyncio.run(ingest_router._find_duplicate(h, "default")) is None
    again = _mk(pg, content_hash=h)
    assert asyncio.run(ingest_router._find_duplicate(h, "default"))["id"] == again
    # ... and the live copy stops the trashed one from coming back.
    resp = as_user(ARCHIVIST()).post(_doc(doc, "/restore"))
    assert resp.status_code == 409
    assert "INV-2026" in resp.json()["detail"]
    # Two live rows with one hash are still impossible.
    with pytest.raises(Exception):
        _mk(pg, content_hash=h)


def test_trash_expires(as_user, pg):
    doc = _mk(pg)
    as_user(ARCHIVIST()).post(_doc(doc, "/trash"))
    _q(
        pg,
        f"UPDATE \"{T}\" SET deleted_at = now() - interval '31 days' WHERE id = CAST(:d AS uuid)",
        d=doc,
    )
    assert as_user(ARCHIVIST()).post(_doc(doc, "/restore")).status_code == 409


# ── 6.3 Retention ─────────────────────────────────────────────────────────────


def _rule(as_user, **body):
    resp = as_user(ARCHIVIST()).post("/api/db/retention-rules", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_rule_precedence_and_recompute(as_user, pg):
    root = _series(pg, "Finances")
    child = _series(pg, "Marchés", root)
    grandchild = _series(pg, "Marchés 2020", child)
    other = _series(pg, "Courrier")
    d_grand = _mk(pg, T2, series_id=grandchild)
    d_root = _mk(pg, T2, series_id=root)
    d_other = _mk(pg, T2, series_id=other)
    d_none = _mk(pg, T2)
    _rule(as_user, name="Type", scope="doc_type", table_name=T2, duration_months=12)
    r_root = _rule(
        as_user, name="Root", scope="series", series_id=root, duration_months=60
    )
    r_child = _rule(
        as_user, name="Child", scope="series", series_id=child, duration_months=24
    )
    ingested = {
        d: _row(pg, d, "ingested_at", T2)[0].date() for d in (d_grand, d_root, d_other)
    }
    import retention

    def until(d):
        return _row(pg, d, "retention_until, retention_rule_id::text", T2)

    # Nearest series first, then the type.
    assert until(d_grand) == (
        retention.add_months(ingested[d_grand], 24),
        r_child["id"],
    )
    assert until(d_root) == (retention.add_months(ingested[d_root], 60), r_root["id"])
    assert until(d_other)[0] == retention.add_months(ingested[d_other], 12)
    assert until(d_none)[0] is not None
    # Changing a rule recomputes.
    resp = as_user(ARCHIVIST()).patch(
        f"/api/db/retention-rules/{r_child['id']}",
        json={"final_action": "keep_forever"},
    )
    assert resp.status_code == 200
    assert until(d_grand) == (None, r_child["id"])
    # Moving the child series under "Courrier" (no rule there) - its own rule still wins;
    # deleting it hands the documents to the type rule.
    assert (
        as_user(ARCHIVIST())
        .delete(f"/api/db/retention-rules/{r_child['id']}")
        .status_code
        == 200
    )
    assert until(d_grand)[1] == r_root["id"]
    move = as_user(ADMIN()).patch(f"/api/db/series/{child}", json={"parent_id": other})
    assert move.status_code == 200, move.text
    assert until(d_grand)[0] == retention.add_months(ingested[d_grand], 12)
    # A document moved into a series gets that series' rule.
    assert (
        as_user(ADMIN())
        .patch(_doc(d_other, "/series", T2), json={"series_id": root})
        .status_code
        == 200
    )
    assert until(d_other)[1] == r_root["id"]
    # One rule per target; only lifecycle managers.
    dup = as_user(ARCHIVIST()).post(
        "/api/db/retention-rules",
        json={"name": "x", "scope": "series", "series_id": root, "duration_months": 1},
    )
    assert dup.status_code == 409
    assert as_user(EDITOR()).get("/api/db/retention-rules").status_code == 403
    assert _audit(pg, "retention_rule_created")


def test_document_date_rule(as_user, pg):
    s = _series(pg, "Actes")
    d1 = _mk(pg, series_id=s, date_acte="8 mars 2020")
    d2 = _mk(pg, series_id=s, date_acte="illisible")
    bad = as_user(ARCHIVIST()).post(
        "/api/db/retention-rules",
        json={
            "name": "x",
            "scope": "series",
            "series_id": s,
            "duration_months": 1,
            "trigger_on": "document_date",
        },
    )
    assert bad.status_code == 422
    _rule(
        as_user,
        name="Actes",
        scope="series",
        series_id=s,
        duration_months=120,
        trigger_on="document_date",
        date_field="date_acte",
    )
    assert _row(pg, d1, "retention_until")[0] == date(2030, 3, 8)
    assert _row(pg, d2, "retention_until")[0] is None
    # Correcting the date recomputes.
    assert (
        as_user(ADMIN())
        .patch(_doc(d2, "/fields"), json={"fields": {"date_acte": "01/02/2021"}})
        .status_code
        == 200
    )
    assert _row(pg, d2, "retention_until")[0] == date(2031, 2, 1)


def test_archived_at_rule_and_daily_job(as_user, pg):
    import retention

    s = _series(pg, "Archives courantes")
    _rule(
        as_user,
        name="Archivage",
        scope="series",
        series_id=s,
        duration_months=0,
        trigger_on="archived_at",
        final_action="review",
    )
    due = _mk(pg, series_id=s)
    active = _mk(pg, series_id=s)
    assert _row(pg, due, "retention_until")[0] is None  # not archived yet
    assert as_user(ARCHIVIST()).post(_doc(due, "/archive")).status_code == 200
    assert _row(pg, due, "retention_until")[0] == datetime.now(timezone.utc).date()
    totals = asyncio.run(retention.run_once(pg))
    assert totals["eligible"] >= 1
    assert _row(pg, due, "lifecycle_status")[0] == "destruction_eligible"
    assert _row(pg, active, "lifecycle_status")[0] == "active"
    notes = _q(
        pg,
        "SELECT payload FROM sdai_notifications WHERE user_id = CAST(:u AS uuid)"
        " AND kind = 'retention_eligible'",
        u=U["archivist"],
    )
    assert any(due in json.dumps(n[0]) for n in notes)
    assert not _q(
        pg,
        "SELECT 1 FROM sdai_notifications WHERE user_id = CAST(:u AS uuid)",
        u=U["editor"],
    )
    assert _audit(pg, "document_destruction_eligible", due)
    eligible = as_user(ARCHIVIST()).get("/api/db/destruction/eligible").json()
    assert any(e["id"] == due and e["final_action"] == "review" for e in eligible)
    # The archivist's review: keep it longer.
    later = (date.today() + timedelta(days=400)).isoformat()
    assert (
        as_user(EDITOR())
        .post(_doc(due, "/retention/extend"), json={"until": later})
        .status_code
        == 403
    )
    assert (
        as_user(ARCHIVIST())
        .post(_doc(due, "/retention/extend"), json={"until": "2000-01-01"})
        .status_code
        == 422
    )
    assert (
        as_user(ARCHIVIST())
        .post(_doc(due, "/retention/extend"), json={"until": later})
        .status_code
        == 200
    )
    assert _row(pg, due, "lifecycle_status, retention_until::text") == (
        "archived",
        later,
    )
    asyncio.run(retention.run_once(pg))  # the manual date wins over the rule
    assert _row(pg, due, "lifecycle_status, retention_until::text") == (
        "archived",
        later,
    )
    assert (
        as_user(ARCHIVIST())
        .post(_doc(due, "/retention/extend"), json={"until": later})
        .status_code
        == 409
    )  # only from destruction_eligible


# ── 6.5 Legal hold ────────────────────────────────────────────────────────────


def test_legal_hold_blocks_everything(as_user, pg):
    import retention

    s = _series(pg, "Contentieux")
    sub = _series(pg, "Affaire X", s)
    held = _mk(pg, series_id=sub)
    eligible = _mk(pg, series_id=sub)
    _set_status(pg, eligible, "destruction_eligible")
    trashed = _mk(pg, series_id=sub)
    as_user(ARCHIVIST()).post(_doc(trashed, "/trash"))
    _q(
        pg,
        f"UPDATE \"{T}\" SET deleted_at = now() - interval '40 days' WHERE id = CAST(:d AS uuid)",
        d=trashed,
    )
    resp = as_user(ARCHIVIST()).post(
        "/api/db/legal-holds", json={"series_id": s, "reason": "Contentieux en cours"}
    )
    assert resp.status_code == 201, resp.text
    hold = resp.json()
    assert _row(pg, held, "legal_hold")[0] is True  # recursive, display flag refreshed
    # Trash, destruction proposal and purge are refused / skipped.
    assert as_user(ARCHIVIST()).post(_doc(held, "/trash")).status_code == 409
    assert (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions",
            json={"documents": [{"table_name": T, "id": eligible}]},
        )
        .status_code
        == 409
    )
    asyncio.run(retention.run_once(pg))
    assert _row(pg, trashed, "lifecycle_status")[0] == "active"
    # A document filed into the held series later is held too.
    late = _mk(pg, series_id=sub)
    assert as_user(ARCHIVIST()).post(_doc(late, "/trash")).status_code == 409
    info = as_user(EDITOR()).get(_doc(late, "/lifecycle")).json()
    assert info["legal_hold"] and info["holds"][0]["reason"] == "Contentieux en cours"
    # Release.
    assert (
        as_user(ARCHIVIST())
        .post(
            f"/api/db/legal-holds/{hold['id']}/release",
            json={"reason": "Jugement définitif"},
        )
        .status_code
        == 200
    )
    assert _row(pg, held, "legal_hold")[0] is False
    assert as_user(ARCHIVIST()).post(_doc(held, "/trash")).status_code == 200
    assert _audit(pg, "legal_hold_set") and _audit(pg, "legal_hold_released")


def test_legal_hold_on_document_and_search(as_user, pg):
    one = _mk(pg, reference_number="PROCES-ALPHA-1")
    two = _mk(pg, reference_number="PROCES-ALPHA-2")
    resp = as_user(ARCHIVIST()).post(
        "/api/db/legal-holds",
        json={"document": {"table_name": T, "id": one}, "reason": "Demande du juge"},
    )
    assert resp.status_code == 201
    assert as_user(ADMIN()).delete(f"/api/review/{T}/{one}").status_code == 409
    search = as_user(ARCHIVIST()).post(
        "/api/db/legal-holds",
        json={"search": {"q": "PROCES-ALPHA-2"}, "reason": "Saisie"},
    )
    assert search.status_code == 201, search.text
    assert search.json()["document_count"] == 1 and search.json()["origin"] == "search"
    assert as_user(ARCHIVIST()).post(_doc(two, "/trash")).status_code == 409
    assert (
        as_user(EDITOR())
        .post(
            "/api/db/legal-holds",
            json={"series_id": _series(pg, "z"), "reason": "nope"},
        )
        .status_code
        == 403
    )
    holds = as_user(ARCHIVIST()).get("/api/db/legal-holds").json()
    assert {h["origin"] for h in holds} >= {"document", "search"}


# ── 6.4 Destruction ───────────────────────────────────────────────────────────


def test_destruction_two_people_tombstone_files_and_pv(as_user, pg):
    from pypdf import PdfReader

    doc = _mk(pg, pages=2, confidentiality="internal")
    linked = _mk(pg)
    assert as_user(ADMIN()).post(
        _doc(linked, "/links"),
        json={"to_table": T, "to_id": doc, "relation": "concerne"},
    ).status_code in (200, 201)
    as_user(ADMIN()).post(_doc(doc, "/tags"), json={"name": "à éliminer"})
    as_user(ADMIN()).patch(_doc(doc, "/fields"), json={"fields": {"amount": "11"}})
    record_id = _row(pg, doc, "record_id")[0]
    files = [ENV[doc]["original"], *ENV[doc]["images"]]
    assert all(os.path.exists(f) for f in files)
    # Only destruction_eligible documents can be proposed.
    assert (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions", json={"documents": [{"table_name": T, "id": doc}]}
        )
        .status_code
        == 409
    )
    _set_status(pg, doc, "destruction_eligible")
    resp = as_user(ADMIN()).post(
        "/api/db/destructions",
        json={"documents": [{"table_name": T, "id": doc}], "comment": "DUA échue"},
    )
    assert resp.status_code == 201, resp.text
    rid = resp.json()["id"]
    # Not twice in pending proposals; the proposer can't approve.
    assert (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions", json={"documents": [{"table_name": T, "id": doc}]}
        )
        .status_code
        == 409
    )
    same = as_user(ADMIN()).post(f"/api/db/destructions/{rid}/approve", json={})
    assert same.status_code == 403
    assert (
        as_user(ARCHIVIST()).post(f"/api/db/destructions/{rid}/approve").status_code
        == 403
    )
    assert _row(pg, doc, "lifecycle_status")[0] == "destruction_eligible"
    # Another admin approves.
    ok = as_user(ADMIN2()).post(
        f"/api/db/destructions/{rid}/approve", json={"note": "vu"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["files"] == len(files)
    # Tombstone: identity, type, dates, hashes stay - content and files go.
    row = _q(
        pg,
        f"SELECT lifecycle_status, record_id, document_type, reference_number, amount,"
        f" original_hash IS NOT NULL, content_hash IS NOT NULL, original_path, page_image_paths,"
        f' destruction_id::text FROM "{T}" WHERE id = CAST(:d AS uuid)',
        d=doc,
    )[0]
    assert row == (
        "destroyed",
        record_id,
        "invoice",
        None,
        None,
        True,
        True,
        None,
        None,
        rid,
    )
    assert not any(os.path.exists(f) for f in files)
    for table in ("sdai_document_versions", "sdai_field_history", "sdai_document_tags"):
        assert (
            _q(
                pg,
                f"SELECT count(*) FROM {table} WHERE document_id = CAST(:d AS uuid)",
                d=doc,
            )[0][0]
            == 0
        )
    assert (
        _q(
            pg,
            "SELECT count(*) FROM batch_document_pages p JOIN batch_documents bd"
            f' ON bd.id = p.batch_document_id JOIN "{T}" d ON d.batch_document_id = bd.id'
            " WHERE d.id = CAST(:d AS uuid)",
            d=doc,
        )[0][0]
        == 0
    )
    destroyed = {
        r[0]
        for r in _q(
            pg,
            "SELECT path FROM sdai_destroyed_files WHERE destruction_id = CAST(:r AS uuid)",
            r=rid,
        )
    }
    assert destroyed == set(files)
    # Links stay and show the tombstone.
    [link] = as_user(ADMIN()).get(_doc(linked, "/links")).json()["links"]
    assert link["other_destroyed"] and link["other_record_id"] == record_id
    assert as_user(ADMIN()).get(_doc(doc)).status_code == 404
    # Record + PV.
    rec = as_user(ARCHIVIST()).get(f"/api/db/destructions/{rid}").json()
    assert rec["status"] == "approved" and rec["proposed_by"] == "admin@example.com"
    assert rec["decided_by"] == "admin2@example.com" and rec["has_pv"]
    assert rec["documents"][0]["versions"][0]["file_hash"]
    pv = as_user(ARCHIVIST()).get(f"/api/db/destructions/{rid}/pv")
    assert pv.status_code == 200 and pv.headers["content-type"] == "application/pdf"
    pdf_text = "".join(
        p.extract_text() for p in PdfReader(io.BytesIO(pv.content)).pages
    )
    assert "Procès-verbal d'élimination" in pdf_text and record_id in pdf_text
    assert "restic" in pdf_text and "Destruction certificate" not in pdf_text
    for action in ("destruction_proposed", "destruction_approved"):
        assert _audit(pg, action)
    assert _audit(pg, "document_destroyed", doc)[0][0]["destruction_id"] == rid
    # The database refuses an approver who is the proposer, whatever the code does.
    with pytest.raises(Exception):
        _q(
            pg,
            "UPDATE sdai_destruction_records SET decided_by = proposed_by WHERE id = CAST(:r AS uuid)",
            r=rid,
        )


def test_pv_has_english_part_for_english_tenant(as_user, pg):
    from pypdf import PdfReader

    _q(
        pg,
        "UPDATE sdai_tenants SET locale = 'en' WHERE id = CAST(:t AS uuid)",
        t=TENANT["id"],
    )
    try:
        doc = _mk(pg)
        _set_status(pg, doc, "destruction_eligible")
        rid = (
            as_user(ARCHIVIST())
            .post(
                "/api/db/destructions",
                json={"documents": [{"table_name": T, "id": doc}]},
            )
            .json()["id"]
        )
        assert (
            as_user(ADMIN()).post(f"/api/db/destructions/{rid}/approve").status_code
            == 200
        )
        pv = as_user(ARCHIVIST()).get(f"/api/db/destructions/{rid}/pv")
        text_ = "".join(
            p.extract_text() for p in PdfReader(io.BytesIO(pv.content)).pages
        )
        assert "Procès-verbal" in text_ and "Destruction certificate" in text_
    finally:
        _q(
            pg,
            "UPDATE sdai_tenants SET locale = NULL WHERE id = CAST(:t AS uuid)",
            t=TENANT["id"],
        )


def test_hold_set_after_proposal_blocks_approval(as_user, pg):
    a, b = _mk(pg), _mk(pg)
    for d in (a, b):
        _set_status(pg, d, "destruction_eligible")
    rid = (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions",
            json={
                "documents": [{"table_name": T, "id": a}, {"table_name": T, "id": b}]
            },
        )
        .json()["id"]
    )
    as_user(ARCHIVIST()).post(
        "/api/db/legal-holds",
        json={"document": {"table_name": T, "id": b}, "reason": "Audit"},
    )
    resp = as_user(ADMIN()).post(f"/api/db/destructions/{rid}/approve")
    assert resp.status_code == 409
    # All or nothing.
    assert _row(pg, a, "lifecycle_status")[0] == "destruction_eligible"
    assert os.path.exists(ENV[a]["original"])
    assert (
        as_user(ARCHIVIST()).get(f"/api/db/destructions/{rid}").json()["status"]
        == "proposed"
    )
    # Rejected / cancelled proposals change nothing.
    assert (
        as_user(ADMIN())
        .post(f"/api/db/destructions/{rid}/reject", json={"note": "non"})
        .status_code
        == 200
    )
    assert _row(pg, a, "lifecycle_status")[0] == "destruction_eligible"


def test_shared_file_is_kept(as_user, pg):
    """A file another live document still uses is never deleted."""
    doc = _mk(pg)
    other = _mk(pg, original_path=ENV[doc]["original"])  # shares the original
    _set_status(pg, doc, "destruction_eligible")
    rid = (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions", json={"documents": [{"table_name": T, "id": doc}]}
        )
        .json()["id"]
    )
    assert (
        as_user(ADMIN()).post(f"/api/db/destructions/{rid}/approve").status_code == 200
    )
    assert os.path.exists(ENV[doc]["original"])
    assert not os.path.exists(ENV[doc]["images"][0])
    assert _row(pg, other, "lifecycle_status")[0] == "active"


def test_trash_purge_is_an_automatic_destruction(as_user, pg):
    import retention

    doc = _mk(pg)
    recent = _mk(pg)
    for d in (doc, recent):
        assert as_user(ARCHIVIST()).post(_doc(d, "/trash")).status_code == 200
    _q(
        pg,
        f"UPDATE \"{T}\" SET deleted_at = now() - interval '31 days' WHERE id = CAST(:d AS uuid)",
        d=doc,
    )
    totals = asyncio.run(retention.run_once(pg))
    assert totals["purged"] >= 1
    assert _row(pg, doc, "lifecycle_status")[0] == "destroyed"
    assert _row(pg, recent, "lifecycle_status")[0] == "active"
    assert not os.path.exists(ENV[doc]["original"])
    rec = _q(
        pg,
        "SELECT kind, status, proposed_by, decided_by, comment, file_path FROM sdai_destruction_records"
        f' WHERE id = (SELECT destruction_id FROM "{T}" WHERE id = CAST(:d AS uuid))',
        d=doc,
    )[0]
    assert rec[:5] == ("trash_purge", "approved", None, None, "purge corbeille")
    assert rec[5] and os.path.exists(rec[5])
    assert doc not in [
        i["id"] for i in as_user(ARCHIVIST()).get("/api/db/trash").json()["items"]
    ]
    assert _audit(pg, "document_destroyed", doc)[0][0]["via"] == "trash_purge"


def test_destroyed_files_are_not_queued_or_received_again(pg):
    import destruction
    from sqlalchemy import text
    from sync import files as sync_files

    path = str(ENV["dir"] / "gone.png")
    open(path, "wb").write(b"x")

    async def run():
        async with pg.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sdai_destroyed_files (tenant_id, destruction_id, path)"
                    " VALUES (CAST(:t AS uuid), gen_random_uuid(), :p)"
                ),
                {"t": TENANT["id"], "p": path},
            )
            await sync_files.enqueue(
                conn,
                [{"op": "insert", "table_name": "x", "row_data": {"image_path": path}}],
                "fetch",
            )
            queued = (
                await conn.execute(
                    text("SELECT count(*) FROM sdai_sync_file_queue WHERE path = :p"),
                    {"p": path},
                )
            ).scalar()
        await destruction.purge_destroyed_files(pg)
        return queued

    assert asyncio.run(run()) == 0
    assert not os.path.exists(path)


def test_tombstone_heals_rows_a_restored_node_sends_back(as_user, pg):
    """A node restored from a snapshot older than a destruction re-sends the
    destroyed document's version / page rows - the sweep removes them."""
    import destruction

    doc = _mk(pg)
    _set_status(pg, doc, "destruction_eligible")
    rid = (
        as_user(ARCHIVIST())
        .post(
            "/api/db/destructions", json={"documents": [{"table_name": T, "id": doc}]}
        )
        .json()["id"]
    )
    assert (
        as_user(ADMIN()).post(f"/api/db/destructions/{rid}/approve").status_code == 200
    )
    _q(
        pg,
        "INSERT INTO sdai_document_versions (tenant_id, table_name, document_id, version_no, file_path)"
        " VALUES (CAST(:t AS uuid), :tb, CAST(:d AS uuid), 1, '/gone.pdf')",
        t=TENANT["id"],
        tb=T,
        d=doc,
    )
    assert asyncio.run(destruction.heal_tombstones(pg)) >= 1
    assert (
        _q(
            pg,
            "SELECT count(*) FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid)",
            d=doc,
        )[0][0]
        == 0
    )
    assert asyncio.run(destruction.heal_tombstones(pg)) == 0
