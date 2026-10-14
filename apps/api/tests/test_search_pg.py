"""Full-text search in page content against a real PostgreSQL (GED Phase 4):
page transcription and its search vector, the backfill, and the document
search over extracted fields OR page text. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_search_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
Tests run in order and build on each other.
"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import api_server
import pytest
from auth import CurrentUser, get_current_user
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

T = "t_default_decree"
U = {k: str(uuid.uuid4()) for k in ("admin", "rev", "reader", "outsider")}
TENANT: dict = {}
BATCH: dict = {}


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


def READER():
    return _user("reader", "reader")


def OUTSIDER():
    return _user("outsider", "reader")


def run(coro):
    return asyncio.run(coro)


async def _q(engine, sql, params=None):
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        return [dict(r) for r in result.mappings().all()] if result.returns_rows else []


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
        for key, role in [
            ("admin", "admin"),
            ("rev", "reviewer"),
            ("reader", "reader"),
            ("outsider", "reader"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid))"
                ),
                {"id": U[key], "e": f"{key}@example.com", "r": role, "t": TENANT["id"]},
            )
        BATCH["id"] = str(
            (
                await conn.execute(
                    text(
                        "INSERT INTO batches (source_type, tenant_id)"
                        " VALUES ('upload', CAST(:t AS uuid)) RETURNING id"
                    ),
                    {"t": TENANT["id"]},
                )
            ).scalar()
        )
        await ingest_router._ensure_table(
            conn, T, {"document_type": "x", "reference_number": "x", "subject": "x"}
        )


async def _upload(engine, filename: str) -> str:
    rows = await _q(
        engine,
        "INSERT INTO batch_documents (batch_id, filename, status)"
        " VALUES (CAST(:b AS uuid), :f, 'completed') RETURNING id::text AS id",
        {"b": BATCH["id"], "f": filename},
    )
    return rows[0]["id"]


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


class _As:
    def __init__(self, client, user):
        self._client, self._user = client, user

    def __getattr__(self, method):
        def call(*args, **kwargs):
            api_server.app.dependency_overrides[get_current_user] = lambda: self._user
            try:
                return getattr(self._client, method)(*args, **kwargs)
            finally:
                api_server.app.dependency_overrides.pop(get_current_user, None)

        return call


@pytest.fixture()
def as_user(pg, dirs):
    with TestClient(api_server.app) as client:
        yield lambda user: _As(client, user)


def _page_image(path: Path, lines: list[str]) -> Path:
    """A clean, large-type page Tesseract reads reliably."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1600, 400 + 120 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 64)
    except OSError:
        try:
            font = ImageFont.truetype(
                "/System/Library/Fonts/Supplemental/Arial.ttf", 64
            )
        except OSError:
            font = ImageFont.load_default(size=64)
    for i, line in enumerate(lines):
        draw.text((100, 150 + 120 * i), line, fill="black", font=font)
    img.save(path)
    return path


# ── 4.1 page text ─────────────────────────────────────────────────────────────


def test_search_config_ignores_accents(pg):
    rows = run(
        _q(
            pg,
            "SELECT to_tsvector('sdai_fr', 'Le Décret présidentiel') @@"
            " websearch_to_tsquery('sdai_fr', 'decret presidentiel') AS hit,"
            " to_tsvector('sdai_fr', 'les décrets') @@ websearch_to_tsquery('sdai_fr', 'DÉCRET') AS stem",
        )
    )
    assert rows[0]["hit"] and rows[0]["stem"]


def test_new_pages_are_transcribed_by_tesseract(pg, tmp_path):
    import ingest_router

    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    bd = run(_upload(pg, "ocr.png"))
    image = _page_image(
        tmp_path / "ocr.png", ["ARRETE MINISTERIEL", "portant nomination"]
    )

    async def go():
        while not ingest_router._page_text_queue.empty():
            ingest_router._page_text_queue.get_nowait()
            ingest_router._page_text_queue.task_done()
        [page_id] = await ingest_router._create_page_rows(bd, [image])
        queued = ingest_router._page_text_queue.get_nowait()
        ingest_router._page_text_queue.task_done()
        assert queued == (page_id, image)
        await ingest_router._transcribe_page(*queued)
        return page_id

    page_id = run(go())
    row = run(
        _q(
            pg,
            "SELECT ocr_text, ocr_tsv @@ websearch_to_tsquery('sdai_fr', 'nomination') AS hit"
            " FROM batch_document_pages WHERE id = CAST(:id AS uuid)",
            {"id": page_id},
        )
    )[0]
    assert "NOMINATION" in row["ocr_text"].upper() and row["hit"]


def test_unreadable_page_stays_untranscribed(pg, tmp_path):
    import ingest_router

    bd = run(_upload(pg, "missing.png"))

    async def go():
        [page_id] = await ingest_router._create_page_rows(
            bd, [tmp_path / "missing.png"]
        )
        await ingest_router._transcribe_page(page_id, tmp_path / "missing.png")
        return page_id

    page_id = run(go())
    row = run(
        _q(
            pg,
            "SELECT ocr_text FROM batch_document_pages WHERE id = CAST(:id AS uuid)",
            {"id": page_id},
        )
    )[0]
    assert row["ocr_text"] is None  # left for the backfill to retry


def test_manual_entry_text_is_searchable_until_the_page_is_redone(pg):
    import ingest_router

    bd = run(_upload(pg, "manual.png"))

    async def go():
        [page_id] = await ingest_router._create_page_rows(
            bd, [Path("/nonexistent/manual.png")]
        )
        await ingest_router._update_page_status(
            page_id,
            "manual",
            fields={
                "subject": "Délibération du conseil",
                "items": [{"label": "Budget annexe"}],
            },
        )
        return page_id

    page_id = run(go())

    def hit(word):
        return run(
            _q(
                pg,
                "SELECT manual_text, ocr_tsv @@ websearch_to_tsquery('sdai_fr', :w) AS hit"
                " FROM batch_document_pages WHERE id = CAST(:id AS uuid)",
                {"id": page_id, "w": word},
            )
        )[0]

    row = hit("deliberation")
    assert row["hit"] and "Budget annexe" in row["manual_text"]
    assert hit("annexe")["hit"]
    run(ingest_router._update_page_status(page_id, "processing"))
    row = hit("deliberation")
    assert row["manual_text"] is None and not row["hit"]


def test_page_text_syncs_without_its_generated_vector(pg):
    from sync.apply import apply_changes

    bd = run(_upload(pg, "sync.png"))
    page = run(
        _q(
            pg,
            "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path, ocr_text)"
            " VALUES (CAST(:bd AS uuid), 1, '/x/sync.png', 'Ordonnance') RETURNING id::text AS id",
            {"bd": bd},
        )
    )[0]["id"]
    captured = run(
        _q(
            pg,
            "SELECT op, row_data FROM sdai_sync_changelog"
            " WHERE table_name = 'batch_document_pages' AND pk ->> 'id' = :id ORDER BY id",
            {"id": page},
        )
    )
    assert captured and all("ocr_tsv" not in c["row_data"] for c in captured)
    assert captured[0]["row_data"]["ocr_text"] == "Ordonnance"

    # A peer's change (even one from older code carrying the vector) applies;
    # the vector is recomputed here from the text.
    async def apply():
        from sqlalchemy import text

        async with pg.begin() as conn:
            clock = (await conn.execute(text("SELECT sdai_hlc_now()"))).scalar()
            peer = "9" + clock[1:13] + ":00001:peer"
            return await apply_changes(
                conn,
                [
                    {
                        "id": 1,
                        "table_name": "batch_document_pages",
                        "pk": {"id": page},
                        "op": "update",
                        "row_data": {"ocr_text": "Circulaire", "ocr_tsv": "'bogus':1"},
                        "base_hlcs": {},
                        "hlc": peer,
                        "origin_node": "peer",
                    }
                ],
            )

    result = run(apply())
    assert not result.errors and result.applied == 1
    row = run(
        _q(
            pg,
            "SELECT ocr_tsv @@ websearch_to_tsquery('sdai_fr', 'circulaire') AS hit,"
            " ocr_tsv @@ websearch_to_tsquery('sdai_fr', 'bogus') AS bogus"
            " FROM batch_document_pages WHERE id = CAST(:id AS uuid)",
            {"id": page},
        )
    )[0]
    assert row["hit"] and not row["bogus"]
    run(_q(pg, "UPDATE sdai_sync_state SET hlc_floor_ms = 0"))


# ── 4.2 backfill ──────────────────────────────────────────────────────────────


def test_backfill_is_resumable_idempotent_and_reports_failures(pg):
    import io

    from scripts.backfill_page_text import backfill

    bd = run(_upload(pg, "old.pdf"))
    for n in (1, 2, 3):
        run(
            _q(
                pg,
                "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path)"
                " VALUES (CAST(:bd AS uuid), :n, :p)",
                {"bd": bd, "n": n, "p": f"/old/page{n}.png"},
            )
        )
    read: list[str] = []

    def fake(path: Path) -> str:
        read.append(path.name)
        if "missing" in str(path) or "nonexistent" in str(path):
            raise FileNotFoundError(path)
        return "" if path.name == "page3.png" else f"Texte de {path.name}"

    before = run(
        _q(pg, "SELECT count(*) AS n FROM batch_document_pages WHERE ocr_text IS NULL")
    )[0]["n"]
    dry = run(backfill(pg, dry_run=True, out=io.StringIO()))
    assert dry["todo"] == before and not read

    # Stopped after one page...
    first = run(
        backfill(pg, batch_size=1, limit=1, sleep=0, transcribe=fake, out=io.StringIO())
    )
    assert first["todo"] == before and len(read) == 1
    # ...and run again: continues with what is still missing.
    second = run(
        backfill(pg, batch_size=2, sleep=0, transcribe=fake, out=io.StringIO())
    )
    assert second["todo"] == before - (first["transcribed"] + first["empty"])
    rows = {
        r["image_path"]: r["ocr_text"]
        for r in run(
            _q(
                pg,
                "SELECT image_path, ocr_text FROM batch_document_pages"
                " WHERE batch_document_id = CAST(:bd AS uuid)",
                {"bd": bd},
            )
        )
    }
    assert rows == {
        "/old/page1.png": "Texte de page1.png",
        "/old/page2.png": "Texte de page2.png",
        "/old/page3.png": "",
    }
    # Unreadable pages stay NULL (reported), so a later run retries only them.
    remaining = run(
        _q(pg, "SELECT image_path FROM batch_document_pages WHERE ocr_text IS NULL")
    )
    assert remaining and all(
        "missing" in r["image_path"] or "nonexistent" in r["image_path"]
        for r in remaining
    )
    read.clear()
    third = run(backfill(pg, sleep=0, transcribe=fake, out=io.StringIO()))
    assert third["todo"] == len(remaining) and third["failed"] == len(remaining)
    assert third["transcribed"] == 0
    # One tenant only.
    assert (
        run(backfill(pg, tenant="nope", dry_run=True, out=io.StringIO()))["todo"] == 0
    )


def test_backfill_refuses_to_run_on_the_cloud_node(monkeypatch):
    from scripts import backfill_page_text

    monkeypatch.setattr(backfill_page_text, "NODE_ROLE", "cloud")
    assert backfill_page_text.main(["--dry-run"]) == 2


# ── 4.3 search ────────────────────────────────────────────────────────────────

D: dict = {}


def _doc(engine, key, *, subject, ref, pages=(), page_list=True, **extra):
    """A filed document with its upload and pages (text per page)."""
    bd = run(_upload(engine, f"{key}.pdf"))
    paths = []
    for n, page in enumerate(pages, start=1):
        path = f"/img/{key}/p{n}.png"
        paths.append(path)
        run(
            _q(
                engine,
                "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path, ocr_text)"
                " VALUES (CAST(:bd AS uuid), :n, :p, :t)",
                {"bd": bd, "n": n, "p": path, "t": page},
            )
        )
    D[key] = str(uuid.uuid4())
    cols = ", ".join(
        [
            "id",
            "subject",
            "reference_number",
            "review_status",
            "batch_document_id",
            "page_image_paths",
            *extra,
        ]
    )
    vals = ", ".join(
        [
            "CAST(:id AS uuid)",
            ":subject",
            ":ref",
            "'approved'",
            "CAST(:bd AS uuid)",
            "CAST(:pages AS jsonb)",
            *(f":{k}" for k in extra),
        ]
    )
    run(
        _q(
            engine,
            f'INSERT INTO "{T}" ({cols}) VALUES ({vals})',
            {
                "id": D[key],
                "subject": subject,
                "ref": ref,
                "bd": bd,
                "pages": json.dumps(paths) if page_list else None,
                **extra,
            },
        )
    )
    return bd


def _search(client, q, **params):
    r = client.get("/api/db/documents", params={"q": q, "page_size": 100, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _hits(client, q, **params):
    return [row["id"] for row in _search(client, q, **params)["results"]]


def test_seed_search_documents(pg):
    _doc(
        pg, "field", subject="Décret portant nomination du directeur", ref="D-2026-001"
    )
    _doc(
        pg,
        "pages",
        subject="Courrier",
        ref="C-2026-002",
        pages=[
            "Page de garde",
            "Objet : ordonnance de paiement n° 45\nMontant en lettres",
        ],
    )
    _doc(
        pg,
        "legacy",  # a row from before page_image_paths: its upload's pages
        subject="Archive",
        ref="A-1990-003",
        pages=["Ancien registre", "", "Une ordonnance royale"],
        page_list=False,
    )
    _doc(
        pg, "restricted", subject="Note", ref="N-1", pages=["ordonnance confidentielle"]
    )
    _doc(pg, "trashed", subject="Brouillon", ref="B-1", pages=["ordonnance supprimée"])
    run(
        _q(
            pg,
            f'UPDATE "{T}" SET deleted_at = now() WHERE id = CAST(:id AS uuid)',
            {"id": D["trashed"]},
        )
    )


def test_accents_and_case_do_not_matter(as_user):
    reader = as_user(READER())
    assert _hits(reader, "decret") == [D["field"]]
    assert _hits(reader, "DÉCRETS") == [D["field"]]
    assert D["field"] in _hits(reader, "nominations directeur")


def test_page_text_matches_with_page_numbers_and_snippets(as_user):
    reader = as_user(READER())
    body = _search(reader, "ordonnance")
    by_id = {row["id"]: row for row in body["results"]}
    assert set(by_id) == {D["pages"], D["legacy"], D["restricted"]}
    pages = by_id[D["pages"]]["search"]["pages"]
    assert [p["page"] for p in pages] == [2]
    segments = pages[0]["snippet"]
    assert {"text": "ordonnance", "hit": True} in segments
    assert "".join(s["text"] for s in segments).startswith("Objet")
    assert by_id[D["pages"]]["search"]["snippet"] == []  # no field matched
    assert [p["page"] for p in by_id[D["legacy"]]["search"]["pages"]] == [3]
    # A phrase, as typed in a search box.
    assert _hits(reader, '"ordonnance de paiement"') == [D["pages"]]
    assert _hits(reader, "ordonnance -paiement -royale -confidentielle") == []


def test_field_matches_rank_above_page_matches(as_user, pg):
    _doc(pg, "fieldhit", subject="Ordonnance de mise en paiement", ref="O-7")
    reader = as_user(READER())
    hits = _hits(reader, "paiement")
    assert hits[0] == D["fieldhit"] and D["pages"] in hits
    top = _search(reader, "paiement")["results"][0]["search"]
    assert {"text": "paiement", "hit": True} in top["snippet"] and top["pages"] == []
    # Other filters still apply on top of the search.
    assert _hits(reader, "paiement", document_type=T) == hits
    assert _hits(reader, "paiement", review_status="pending") == []


def test_search_never_shows_what_the_caller_cannot_read(as_user):
    admin = as_user(ADMIN())
    r = admin.post(
        f"/api/db/documents/{T}/{D['restricted']}/access",
        json={"grantee_type": "user", "grantee_id": U["reader"]},
    )
    assert r.status_code == 200, r.text
    assert D["restricted"] in _hits(as_user(READER()), "confidentielle")
    outsider = as_user(OUTSIDER())
    assert _hits(outsider, "confidentielle") == []
    body = _search(outsider, "ordonnance")
    assert D["restricted"] not in {row["id"] for row in body["results"]}
    assert body["total"] == len(body["results"])  # the count leaks nothing either
    # Trashed documents are never found, admins included.
    assert _hits(admin, "supprimée") == []


def test_only_the_current_version_pages_count(as_user, pg):
    # Re-extraction into the same row: the new version's upload names the
    # document, page_image_paths now lists its pages.
    upload = run(_upload(pg, "v2.pdf"))
    run(
        _q(
            pg,
            "UPDATE batch_documents SET target_table = :t, target_document_id = CAST(:d AS uuid),"
            " target_version = 2 WHERE id = CAST(:bd AS uuid)",
            {"t": T, "d": D["pages"], "bd": upload},
        )
    )
    run(
        _q(
            pg,
            "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path, ocr_text)"
            " VALUES (CAST(:bd AS uuid), 1, '/img/v2/p1.png', 'Mandat de virement rectifié')",
            {"bd": upload},
        )
    )
    run(
        _q(
            pg,
            f"""UPDATE "{T}" SET page_image_paths = '["/img/v2/p1.png"]' WHERE id = CAST(:id AS uuid)""",
            {"id": D["pages"]},
        )
    )
    reader = as_user(READER())
    found = _search(reader, "virement")["results"]
    assert [r["id"] for r in found] == [D["pages"]]
    assert [p["page"] for p in found[0]["search"]["pages"]] == [1]
    # The replaced version's text no longer finds the document.
    assert D["pages"] not in _hits(reader, '"ordonnance de paiement"')


def test_search_without_query_is_unchanged(as_user):
    body = _search(as_user(READER()), "")
    assert body["results"] and all("search" not in row for row in body["results"])
