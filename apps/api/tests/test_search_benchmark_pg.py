"""Full-text search and list benchmark (GED Phases 4 and 9): 2 000 documents
x 5 pages = 10 000 transcribed pages (BENCH_DOCS=20000 for the 100 000-page
run of Phase 9), searched and listed through GET /api/db/documents the way
the UI does (access clause, ranking, snippets for one page of results).

Every document is restricted like in production: the uploader's automatic
grant, a type grant to an "all" group (the plain reader below is in it), one
series grant per sub-series of a 10 x 5 filing plan (the grant reader holds 2
of the 20 groups, ~12 % of the documents) and direct grants on 1 %.
Opt-in, needs a throwaway PostgreSQL:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        [BENCH_DOCS=20000] pytest apps/api/tests/test_search_benchmark_pg.py -m benchmark -s

The database is wiped (public schema dropped) - never point this at real data.
Not part of `-m pg` (it takes a while to seed).
"""

import asyncio
import os
import statistics
import time
import uuid

import api_server
import pytest
from auth import CurrentUser, get_current_user
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

T = "t_default_courrier"
DOCS = int(os.getenv("BENCH_DOCS", "2000"))
PAGES_PER_DOC = 5
WORDS_PER_PAGE = 180
# Generous ceilings - the point is catching a plan that stops using the
# indexes (seconds), not measuring this machine.
MAX_MEDIAN_SECONDS = 1.0
TENANT: dict = {}
USER = str(uuid.uuid4())
GRANT_USER = str(uuid.uuid4())
UPLOADER = str(uuid.uuid4())
GROUPS: dict = {}

# Common administrative French; "trésorerie" is rare (one page in 500).
_VOCAB = (
    "le la les de des du un une et en au aux pour par sur dans avec ministère direction service"
    " décret arrêté décision note courrier demande réponse objet référence monsieur madame"
    " directeur général préfet commune région budget paiement mandat ordonnance dépense recette"
    " personnel agent nomination mutation congé formation contrat marché fourniture travaux"
    " rapport réunion conseil délibération séance président secrétaire signature date annexe"
)


def run(coro):
    return asyncio.run(coro)


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
        await conn.execute(
            text(
                "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                " VALUES (CAST(:id AS uuid), 'reader@example.com', 'x', 'reader', CAST(:t AS uuid))"
            ),
            {"id": USER, "t": TENANT["id"]},
        )
        await ingest_router._ensure_table(
            conn, T, {"document_type": "x", "reference_number": "x", "subject": "x"}
        )
        batch = (
            await conn.execute(
                text(
                    "INSERT INTO batches (source_type, tenant_id)"
                    " VALUES ('upload', CAST(:t AS uuid)) RETURNING id"
                ),
                {"t": TENANT["id"]},
            )
        ).scalar()
        # Seeding writes are not what is measured: skip sync capture.
        await conn.execute(text("SET LOCAL sdai.applying_sync = on"))
        await conn.execute(text("SELECT setseed(0.42)"))
        await conn.execute(
            text(f"""
            WITH vocab AS (SELECT string_to_array(:vocab, ' ') AS w),
            uploads AS (
                INSERT INTO batch_documents (batch_id, filename, status)
                SELECT CAST(:b AS uuid), 'doc' || g || '.pdf', 'completed'
                FROM generate_series(1, :docs) g
                RETURNING id, filename
            ),
            pages AS (
                INSERT INTO batch_document_pages (batch_document_id, page_number, image_path, ocr_text, status)
                SELECT u.id, n, '/bench/' || u.filename || '/p' || n || '.png',
                       (SELECT string_agg(
                                   CASE WHEN random() < 0.00001 THEN 'trésorerie'
                                        ELSE w[1 + floor(random() * array_length(w, 1))::int] END,
                                   ' ')
                        FROM vocab, generate_series(1, :words) k
                        WHERE n > 0 AND u.id IS NOT NULL),
                       'completed'
                FROM uploads u, generate_series(1, :pages) n
                RETURNING batch_document_id, page_number, image_path
            )
            INSERT INTO "{T}" (subject, reference_number, review_status, batch_document_id, page_image_paths)
            SELECT 'Courrier ' || u.filename, 'REF-' || row_number() OVER (),
                   'approved', u.id,
                   (SELECT jsonb_agg(p.image_path ORDER BY p.page_number)
                    FROM pages p WHERE p.batch_document_id = u.id)
            FROM uploads u
            """),
            {
                "vocab": _VOCAB,
                "b": str(batch),
                "docs": DOCS,
                "pages": PAGES_PER_DOC,
                "words": WORDS_PER_PAGE,
            },
        )
        await _seed_grants(conn)
        await conn.execute(text("ANALYZE batch_document_pages"))
        await conn.execute(text(f'ANALYZE "{T}"'))
        await conn.execute(text("ANALYZE sdai_document_access"))
        await conn.execute(text("ANALYZE sdai_series"))


async def _seed_grants(conn):
    from sqlalchemy import text

    p = {"t": TENANT["id"], "u": UPLOADER, "g": GRANT_USER}
    await conn.execute(
        text(
            "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id) VALUES"
            " (CAST(:u AS uuid), 'uploader@example.com', 'x', 'reviewer', CAST(:t AS uuid)),"
            " (CAST(:g AS uuid), 'granted@example.com', 'x', 'reader', CAST(:t AS uuid))"
        ),
        p,
    )
    rows = await conn.execute(
        text(
            "INSERT INTO sdai_groups (tenant_id, name)"
            " SELECT CAST(:t AS uuid), n FROM unnest(ARRAY['all'] ||"
            " ARRAY(SELECT 'g' || i FROM generate_series(0, 19) i)) n"
            " RETURNING name, id::text"
        ),
        p,
    )
    GROUPS.update({name: gid for name, gid in rows})
    await conn.execute(
        text(
            "INSERT INTO sdai_user_groups (user_id, group_id) VALUES"
            " (CAST(:r AS uuid), CAST(:all AS uuid)),"
            " (CAST(:g AS uuid), CAST(:g0 AS uuid)), (CAST(:g AS uuid), CAST(:g1 AS uuid))"
        ),
        {
            "r": USER,
            "g": GRANT_USER,
            "all": GROUPS["all"],
            "g0": GROUPS["g0"],
            "g1": GROUPS["g1"],
        },
    )
    # Filing plan: 10 series x 5 sub-series, materialized paths
    await conn.execute(
        text(
            "INSERT INTO sdai_series (id, tenant_id, name, code, path)"
            " SELECT x.id, CAST(:t AS uuid), 'Série ' || r, r::text, '/' || x.id::text || '/'"
            " FROM generate_series(1, 10) r, LATERAL (SELECT gen_random_uuid() AS id, r AS k) x"
        ),
        p,
    )
    await conn.execute(
        text(
            "INSERT INTO sdai_series (id, tenant_id, name, code, parent_id, path)"
            " SELECT x.id, r.tenant_id, 'Sous-série ' || r.code || '.' || c, r.code || '.' || c,"
            "        r.id, r.path || x.id::text || '/'"
            " FROM sdai_series r, generate_series(1, 5) c,"
            "      LATERAL (SELECT gen_random_uuid() AS id, c AS k) x"
            " WHERE r.tenant_id = CAST(:t AS uuid) AND r.parent_id IS NULL"
            "   AND r.system_key IS NULL"
        ),
        p,
    )
    children = (
        "SELECT array_agg(id ORDER BY code) AS a FROM sdai_series"
        " WHERE tenant_id = CAST(:t AS uuid) AND parent_id IS NOT NULL"
    )
    await conn.execute(
        text(
            f'WITH s AS ({children}) UPDATE "{T}" d'
            f" SET series_id = s.a[1 + abs(hashtext(d.id::text)) % 50] FROM s"
        ),
        p,
    )
    # Sub-series i -> group g(i mod 20); type grant -> "all"
    await conn.execute(
        text(
            "INSERT INTO sdai_document_access"
            " (tenant_id, grantee_type, grantee_id, scope, series_id, permission)"
            " SELECT CAST(:t AS uuid), 'group', g.id, 'series', s.id, 'read'"
            " FROM (SELECT id, row_number() OVER (ORDER BY code) - 1 AS i FROM sdai_series"
            "       WHERE tenant_id = CAST(:t AS uuid) AND parent_id IS NOT NULL) s"
            " JOIN sdai_groups g ON g.tenant_id = CAST(:t AS uuid) AND g.name = 'g' || (s.i % 20)"
        ),
        p,
    )
    await conn.execute(
        text(
            "INSERT INTO sdai_document_access"
            " (tenant_id, table_name, grantee_type, grantee_id, scope, permission)"
            " VALUES (CAST(:t AS uuid), :tbl, 'group', CAST(:all AS uuid), 'doc_type', 'read')"
        ),
        {**p, "tbl": T, "all": GROUPS["all"]},
    )
    # Uploader's automatic grant on every document, direct grants on 1 %
    await conn.execute(
        text(
            "INSERT INTO sdai_document_access"
            " (tenant_id, table_name, document_id, grantee_type, grantee_id, scope, permission)"
            f" SELECT CAST(:t AS uuid), :tbl, id, 'user', CAST(:u AS uuid), 'document', 'edit'"
            f' FROM "{T}"'
            f" UNION ALL SELECT CAST(:t AS uuid), :tbl, id, 'user', CAST(:g AS uuid), 'document',"
            f" 'read' FROM \"{T}\" WHERE abs(hashtext(id::text || 'direct')) % 100 = 0"
        ),
        {**p, "tbl": T},
    )


@pytest.fixture(scope="module")
def pg():
    import ingest_router
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    saved = (ingest_router.engine, ingest_router.SessionLocal)
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    t0 = time.monotonic()
    asyncio.run(_seed(engine))
    print(f"\nseeded {DOCS * PAGES_PER_DOC} pages in {time.monotonic() - t0:.1f} s")
    yield engine
    ingest_router.engine, ingest_router.SessionLocal = saved


@pytest.fixture()
def reader(pg, dirs):
    user = CurrentUser(
        id=USER,
        email="reader@example.com",
        full_name="reader",
        role="reader",
        tenant_id=TENANT["id"],
        tenant_slug="default",
        tenant_name="Default",
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[GROUPS["all"]],
        locale="en",
    )
    api_server.app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(api_server.app) as client:
            yield client
    finally:
        api_server.app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "q",
    [
        "trésorerie",  # rare: a handful of pages
        "decret",  # accent-free spelling of a common word
        '"ordonnance de paiement"',  # phrase
        "budget -personnel",  # exclusion
        "courrier",  # field match on every document (worst case)
    ],
)
def test_search_10k_pages(reader, q):
    timings = []
    body = None
    for _ in range(5):
        t0 = time.perf_counter()
        r = reader.get("/api/db/documents", params={"q": q, "page_size": 24})
        timings.append(time.perf_counter() - t0)
        assert r.status_code == 200, r.text
        body = r.json()
    median = statistics.median(timings)
    print(f"\n  q={q!r}: {body['total']} documents, median {median * 1000:.0f} ms")
    assert body["results"], q
    assert median < MAX_MEDIAN_SECONDS, f"{q!r}: median {median:.2f} s"


def test_page_text_search_uses_the_gin_index(pg):
    from sqlalchemy import text

    async def plan():
        async with pg.connect() as conn:
            rows = await conn.execute(
                text(
                    "EXPLAIN SELECT batch_document_id FROM batch_document_pages"
                    " WHERE ocr_tsv @@ websearch_to_tsquery('sdai_fr', 'trésorerie')"
                )
            )
            return "\n".join(r[0] for r in rows)

    assert "idx_batch_document_pages_ocr_tsv" in run(plan())


@pytest.fixture()
def grant_reader(pg, dirs):
    user = CurrentUser(
        id=GRANT_USER,
        email="granted@example.com",
        full_name="granted",
        role="reader",
        tenant_id=TENANT["id"],
        tenant_slug="default",
        tenant_name="Default",
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[GROUPS["g0"], GROUPS["g1"]],
        locale="en",
    )
    api_server.app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(api_server.app) as client:
            yield client
    finally:
        api_server.app.dependency_overrides.pop(get_current_user, None)


def _root_series(pg) -> str:
    from sqlalchemy import text

    async def first_root():
        async with pg.connect() as conn:
            return str(
                (
                    await conn.execute(
                        text(
                            "SELECT id FROM sdai_series WHERE parent_id IS NULL"
                            " AND system_key IS NULL ORDER BY code LIMIT 1"
                        )
                    )
                ).scalar()
            )

    return run(first_root())


@pytest.mark.parametrize(
    "case",
    ["list", "deep page", "search", "series subtree", "all-docs reader list"],
)
def test_list_with_grants(pg, grant_reader, case):
    params = {"page_size": 24}
    if case == "deep page":
        params["page"] = max(2, DOCS // 240)  # ~the last tenth of the visible pages
    elif case == "search":
        params["q"] = "budget"
    elif case == "series subtree":
        params.update(series=_root_series(pg), include_subseries="true")
    client = grant_reader
    if case == "all-docs reader list":
        api_server.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=USER,
            email="reader@example.com",
            full_name="reader",
            role="reader",
            tenant_id=TENANT["id"],
            tenant_slug="default",
            tenant_name="Default",
            can_manage_access=False,
            can_edit_extraction=False,
            group_ids=[GROUPS["all"]],
            locale="en",
        )
    timings = []
    body = None
    for _ in range(5):
        t0 = time.perf_counter()
        r = client.get("/api/db/documents", params=params)
        timings.append(time.perf_counter() - t0)
        assert r.status_code == 200, r.text
        body = r.json()
    median = statistics.median(timings)
    print(
        f"\n  {case}: {body['total']} visible documents, median {median * 1000:.0f} ms"
    )
    if case == "all-docs reader list":
        assert body["total"] == DOCS
    elif case == "series subtree":
        # series 1: its sub-series 1.1 and 1.2 are granted to g0 / g1
        assert 0 < body["total"] < 0.05 * DOCS, body["total"]
    else:
        # ~12 % via 2 of 20 groups' series grants + ~1 % direct grants
        assert 0.05 * DOCS < body["total"] < 0.25 * DOCS, body["total"]
    assert median < MAX_MEDIAN_SECONDS, f"{case}: median {median:.2f} s"
