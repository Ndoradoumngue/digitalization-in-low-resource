"""Plain upload and extraction on demand against a real PostgreSQL (GED
Phase 10). Opt-in, like test_access_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_plain_upload_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import io
import os
import uuid

import api_server
import pytest
from auth import CurrentUser, get_current_user
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

GENERIC = "t_default_document"
INVOICE = "t_default_invoice"

U = {k: str(uuid.uuid4()) for k in ("admin", "editor", "reader")}
TENANT: dict = {}
STATE: dict = {}


def _user(key, role) -> CurrentUser:
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
    )


def ADMIN():
    return _user("admin", "admin")


def EDITOR():
    return _user("editor", "reviewer")


def READER():
    return _user("reader", "reader")


def _png(color=(200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "PNG")
    return buf.getvalue()


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
        for key, role in (
            ("admin", "admin"),
            ("editor", "reviewer"),
            ("reader", "reader"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid))"
                ),
                {"id": U[key], "e": f"{key}@example.com", "r": role, "t": TENANT["id"]},
            )
        for key, name in (("archives", "Archives"), ("invoices", "Invoices")):
            STATE[key] = str(
                (
                    await conn.execute(
                        text(
                            "INSERT INTO sdai_series (tenant_id, name)"
                            " VALUES (CAST(:t AS uuid), :n) RETURNING id"
                        ),
                        {"t": TENANT["id"], "n": name},
                    )
                ).scalar()
            )
        # Filing rule for invoices - applies to a plain document extracted
        # out of "À classer".
        await conn.execute(
            text(
                "INSERT INTO sdai_type_series_defaults (tenant_id, table_name, series_id)"
                " VALUES (CAST(:t AS uuid), :tb, CAST(:s AS uuid))"
            ),
            {"t": TENANT["id"], "tb": INVOICE, "s": STATE["invoices"]},
        )


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import documents_router
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    data_dir = tmp_path_factory.mktemp("data")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    saved = (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.UPLOADS_DIR,
        ingest_router.IMAGES_DIR,
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [data_dir.resolve()]
    ingest_router.UPLOADS_DIR = data_dir / "uploads"
    ingest_router.IMAGES_DIR = data_dir / "images"
    asyncio.run(_seed(engine))
    yield engine
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.UPLOADS_DIR,
        ingest_router.IMAGES_DIR,
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
        async with pg.begin() as conn:
            return (await conn.execute(text(sql), params)).all()

    return asyncio.run(run())


def _drain_queue():
    import ingest_router

    items = []
    while not ingest_router._queue.empty():
        items.append(ingest_router._queue.get_nowait())
    return items


def _plain_upload(client, name, *, series_id=None, color=(200, 30, 30)):
    """Upload one PNG without extraction and run its Stage 1; returns the
    filed document's id."""
    import ingest_router

    _drain_queue()
    data = {"extract": "false"}
    if series_id:
        data["series_id"] = series_id
    resp = client.post(
        "/api/ingest/upload",
        files=[("files", (name, _png(color), "image/png"))],
        data=data,
    )
    assert resp.status_code == 200, resp.text
    [item] = _drain_queue()
    asyncio.run(ingest_router._stage1_process(*item))
    STATE.setdefault("batch_documents", {})[name] = item[0]
    return resp.json()["batch_id"], item[0]


def _doc_of(pg, batch_document_id):
    [(doc_id,)] = _q(
        pg,
        f'SELECT id::text FROM "{GENERIC}" WHERE batch_document_id = CAST(:bd AS uuid)',
        bd=batch_document_id,
    )
    return doc_id


# ── 10.2 Plain upload ─────────────────────────────────────────────────────────


def test_plain_upload_files_document_without_extraction(as_user, pg):
    import ingest_router

    while not ingest_router._preprocessed_queue.empty():
        ingest_router._preprocessed_queue.get_nowait()
    _, bd = _plain_upload(as_user(EDITOR()), "deed.png", series_id=STATE["archives"])
    # Stage 2 (the VLM) never sees it.
    assert ingest_router._preprocessed_queue.empty()
    [(status, doc_type)] = _q(
        pg,
        "SELECT status, document_type FROM batch_documents WHERE id = CAST(:bd AS uuid)",
        bd=bd,
    )
    assert (status, doc_type) == ("completed", "document")
    doc_id = _doc_of(pg, bd)
    STATE["deed"] = doc_id
    [(review, series, record_id, uploaded_by, original)] = _q(
        pg,
        f"SELECT review_status, series_id::text, record_id, uploaded_by::text, original_path"
        f' FROM "{GENERIC}" WHERE id = CAST(:id AS uuid)',
        id=doc_id,
    )
    assert review == "not_extracted"
    assert series == STATE["archives"]
    assert record_id
    assert uploaded_by == U["editor"]
    assert original and original.endswith("deed.png")
    pages = _q(
        pg,
        "SELECT status FROM batch_document_pages WHERE batch_document_id = CAST(:bd AS uuid)",
        bd=bd,
    )
    assert pages == [("stored",)]
    # Private to its uploader, like an approved document; version 1 recorded.
    assert _q(
        pg,
        "SELECT grantee_id::text, permission FROM sdai_document_access"
        " WHERE table_name = :t AND document_id = CAST(:id AS uuid)",
        t=GENERIC,
        id=doc_id,
    ) == [(U["editor"], "edit")]
    assert _q(
        pg,
        "SELECT version_no FROM sdai_document_versions"
        " WHERE table_name = :t AND document_id = CAST(:id AS uuid)",
        t=GENERIC,
        id=doc_id,
    ) == [(1,)]
    # Never in the review queue.
    assert as_user(ADMIN()).get("/api/review/count").json()["pending"] == 0


def test_plain_upload_without_category_goes_to_be_filed(as_user, pg):
    _, bd = _plain_upload(as_user(EDITOR()), "unsorted.png", color=(10, 120, 10))
    doc_id = _doc_of(pg, bd)
    STATE["unsorted"] = doc_id
    [(key,)] = _q(
        pg,
        f'SELECT s.system_key FROM "{GENERIC}" d JOIN sdai_series s ON s.id = d.series_id'
        " WHERE d.id = CAST(:id AS uuid)",
        id=doc_id,
    )
    assert key == "to_be_filed"


def test_not_extracted_filter(as_user):
    body = as_user(ADMIN()).get("/api/db/documents?review_status=not_extracted").json()
    assert {r["id"] for r in body["results"]} == {STATE["deed"], STATE["unsorted"]}


def test_upload_into_unknown_category_refused(as_user):
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        resp = as_user(EDITOR()).post(
            "/api/ingest/upload",
            files=[("files", ("x.png", _png(), "image/png"))],
            data={"extract": "false", "series_id": bad},
        )
        assert resp.status_code == 422, resp.text


def test_page_actions_refused_on_plain_upload(as_user, pg):
    [(page_id,)] = _q(
        pg,
        "SELECT id::text FROM batch_document_pages WHERE batch_document_id = CAST(:bd AS uuid)",
        bd=STATE["batch_documents"]["deed.png"],
    )
    resp = as_user(EDITOR()).post(f"/api/ingest/pages/{page_id}/skip")
    assert resp.status_code == 409, resp.text


# ── 10.3 Extraction on demand ─────────────────────────────────────────────────


def _seed_references(pg, doc_id):
    """One row per kind of reference to the document, so the move must
    re-point each of them."""
    tag = _q(
        pg,
        "INSERT INTO sdai_tags (tenant_id, name) VALUES (CAST(:t AS uuid), 'urgent') RETURNING id::text",
        t=TENANT["id"],
    )[0][0]
    _q(
        pg,
        "INSERT INTO sdai_document_tags (tenant_id, table_name, document_id, tag_id)"
        " VALUES (CAST(:t AS uuid), :tb, CAST(:id AS uuid), CAST(:tag AS uuid)) RETURNING 1",
        t=TENANT["id"],
        tb=GENERIC,
        id=doc_id,
        tag=tag,
    )
    _q(
        pg,
        "INSERT INTO sdai_comments (tenant_id, table_name, document_id, author_id, body)"
        " VALUES (CAST(:t AS uuid), :tb, CAST(:id AS uuid), CAST(:u AS uuid), 'see p.2') RETURNING 1",
        t=TENANT["id"],
        tb=GENERIC,
        id=doc_id,
        u=U["admin"],
    )
    _q(
        pg,
        "INSERT INTO sdai_notifications (tenant_id, user_id, kind, table_name, document_id)"
        " VALUES (CAST(:t AS uuid), CAST(:u AS uuid), 'mention', :tb, CAST(:id AS uuid)) RETURNING 1",
        t=TENANT["id"],
        u=U["admin"],
        tb=GENERIC,
        id=doc_id,
    )
    other = STATE["unsorted"]
    for frm, frm_id, to, to_id in (
        (GENERIC, doc_id, GENERIC, other),
        (GENERIC, other, GENERIC, doc_id),
    ):
        _q(
            pg,
            "INSERT INTO sdai_document_links (tenant_id, from_table, from_id, to_table, to_id, relation)"
            " VALUES (CAST(:t AS uuid), :f, CAST(:fi AS uuid), :to, CAST(:ti AS uuid), 'cites') RETURNING 1",
            t=TENANT["id"],
            f=frm,
            fi=frm_id,
            to=to,
            ti=to_id,
        )


def test_reader_cannot_extract(as_user):
    resp = as_user(READER()).post(
        f"/api/db/documents/{GENERIC}/{STATE['deed']}/extract"
    )
    assert resp.status_code in (403, 404), resp.text


def test_extract_queues_targeted_upload(as_user, pg):
    import ingest_router

    _seed_references(pg, STATE["deed"])
    _drain_queue()
    resp = as_user(EDITOR()).post(
        f"/api/db/documents/{GENERIC}/{STATE['deed']}/extract"
    )
    assert resp.status_code == 200, resp.text
    [item] = _drain_queue()
    [(target_table, target_doc, source_type, extract)] = _q(
        pg,
        "SELECT bd.target_table, bd.target_document_id::text, b.source_type, bd.extract"
        " FROM batch_documents bd JOIN batches b ON b.id = bd.batch_id"
        " WHERE bd.id = CAST(:bd AS uuid)",
        bd=item[0],
    )
    assert (target_table, target_doc, source_type, extract) == (
        GENERIC,
        STATE["deed"],
        "extract",
        True,
    )
    STATE["extract_bd"] = item[0]
    assert asyncio.run(ingest_router._reextract_target(item[0])) == (
        GENERIC,
        STATE["deed"],
        1,
    )
    # A second request while it runs is refused.
    again = as_user(EDITOR()).post(
        f"/api/db/documents/{GENERIC}/{STATE['deed']}/extract"
    )
    assert again.status_code == 409


def test_first_extraction_moves_document_into_detected_type(as_user, pg):
    import ingest_router
    import relocate

    [(record_id,)] = _q(
        pg,
        f'SELECT record_id FROM "{GENERIC}" WHERE id = CAST(:id AS uuid)',
        id=STATE["deed"],
    )
    target = asyncio.run(ingest_router._reextract_target(STATE["extract_bd"]))
    applied = asyncio.run(
        ingest_router._apply_reextraction(
            STATE["extract_bd"],
            target,
            {
                "document_type": "invoice",
                "reference_number": "INV-7",
                "amount": "120",
                "extraction_confidence": "medium",
            },
            confidence="medium",
            review_status="review_required",
            primary_image="/p/1.png",
            page_image_paths=["/p/1.png"],
            source_pdf_path=None,
        )
    )
    assert applied["table_name"] == INVOICE
    assert (
        _q(
            pg,
            f'SELECT 1 FROM "{GENERIC}" WHERE id = CAST(:id AS uuid)',
            id=STATE["deed"],
        )
        == []
    )
    [(rid, series, review, ref, doc_type)] = _q(
        pg,
        f"SELECT record_id, series_id::text, review_status, reference_number, document_type"
        f' FROM "{INVOICE}" WHERE id = CAST(:id AS uuid)',
        id=STATE["deed"],
    )
    # Same identity, category chosen at upload kept (not "to be filed").
    assert (rid, series) == (record_id, STATE["archives"])
    assert (review, ref, doc_type) == ("review_required", "INV-7", "invoice")
    # Every reference followed it.
    for table, table_col, id_col in relocate.REFERENCES:
        stale = _q(
            pg,
            f'SELECT count(*) FROM "{table}" WHERE "{table_col}" = :t AND "{id_col}" = CAST(:id AS uuid)',
            t=GENERIC,
            id=STATE["deed"],
        )
        assert stale == [(0,)], table
    for table in (
        "sdai_document_access",
        "sdai_document_versions",
        "sdai_document_tags",
        "sdai_comments",
        "sdai_notifications",
    ):
        assert _q(
            pg,
            f"SELECT count(*) FROM {table} WHERE table_name = :t AND document_id = CAST(:id AS uuid)",
            t=INVOICE,
            id=STATE["deed"],
        ) == [(1,)], table
    assert _q(
        pg,
        "SELECT count(*) FROM sdai_document_links WHERE (from_table = :t AND from_id = CAST(:id AS uuid))"
        " OR (to_table = :t AND to_id = CAST(:id AS uuid))",
        t=INVOICE,
        id=STATE["deed"],
    ) == [(2,)]
    [(details,)] = _q(
        pg,
        "SELECT details FROM sdai_audit_log WHERE action = 'document_type_assigned'"
        " AND document_id = CAST(:id AS uuid)",
        id=STATE["deed"],
    )
    assert (details["from_table"], details["to_table"]) == (GENERIC, INVOICE)
    # Now in the review queue.
    assert as_user(ADMIN()).get("/api/review/count").json()["pending"] == 1


def test_moved_document_keeps_history_and_can_be_located(as_user):
    editor = as_user(EDITOR())
    # The old address is gone; the location endpoint finds the new one.
    assert editor.get(f"/api/db/documents/{GENERIC}/{STATE['deed']}").status_code == 404
    loc = editor.get(f"/api/db/document-location/{STATE['deed']}")
    assert loc.status_code == 200 and loc.json() == {"table_name": INVOICE}
    assert (
        as_user(READER()).get(f"/api/db/document-location/{STATE['deed']}").status_code
        == 404
    )
    # Audit entries written under the old table still show.
    history = editor.get(f"/api/db/documents/{INVOICE}/{STATE['deed']}/history").json()
    actions = {e["action"] for e in history["events"]}
    assert {
        "document_ingested",
        "document_type_assigned",
        "document_reextracted",
    } <= actions
    # And it can't be "extracted" a second time.
    again = editor.post(f"/api/db/documents/{INVOICE}/{STATE['deed']}/extract")
    assert again.status_code == 409


def test_extraction_out_of_to_be_filed_applies_filing_rule(as_user, pg):
    import ingest_router

    _drain_queue()
    resp = as_user(EDITOR()).post(
        "/api/db/documents/extract",
        json={
            "documents": [
                {"table_name": GENERIC, "id": STATE["unsorted"]},
                {"table_name": INVOICE, "id": STATE["deed"]},  # already extracted
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["started"] == 1
    assert "error" in body["results"][1]
    [item] = _drain_queue()
    target = asyncio.run(ingest_router._reextract_target(item[0]))
    asyncio.run(
        ingest_router._apply_reextraction(
            item[0],
            target,
            {
                "document_type": "invoice",
                "reference_number": "INV-8",
                "extraction_confidence": "high",
            },
            confidence="high",
            review_status="auto_approved",
        )
    )
    [(series,)] = _q(
        pg,
        f'SELECT series_id::text FROM "{INVOICE}" WHERE id = CAST(:id AS uuid)',
        id=STATE["unsorted"],
    )
    assert series == STATE["invoices"]


def test_extraction_detecting_generic_type_stays_in_place(as_user, pg):
    import ingest_router

    _, bd = _plain_upload(as_user(EDITOR()), "letter.png", color=(0, 0, 200))
    doc_id = _doc_of(pg, bd)
    _drain_queue()
    assert (
        as_user(EDITOR())
        .post(f"/api/db/documents/{GENERIC}/{doc_id}/extract")
        .status_code
        == 200
    )
    [item] = _drain_queue()
    target = asyncio.run(ingest_router._reextract_target(item[0]))
    applied = asyncio.run(
        ingest_router._apply_reextraction(
            item[0],
            target,
            {
                "document_type": "document",
                "subject": "hello",
                "extraction_confidence": "low",
            },
            confidence="low",
            review_status="review_required",
        )
    )
    assert applied["table_name"] == GENERIC
    [(review, subject)] = _q(
        pg,
        f'SELECT review_status, subject FROM "{GENERIC}" WHERE id = CAST(:id AS uuid)',
        id=doc_id,
    )
    assert (review, subject) == ("review_required", "hello")
