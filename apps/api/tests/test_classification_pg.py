"""Classification against a real PostgreSQL (GED Phase 3): the series tree,
inherited series grants, subtree filters, tags, automatic filing and link
suggestions. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_classification_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
Tests run in order and build on each other.
"""

import asyncio
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

T = "t_default_invoice"
U = {k: str(uuid.uuid4()) for k in ("admin", "rev", "grouped", "outsider")}
G = str(uuid.uuid4())
D = {k: str(uuid.uuid4()) for k in ("a1a", "b", "none")}
S: dict = {}
TENANT: dict = {}


def _user(key, role, groups=()) -> CurrentUser:
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
        group_ids=list(groups),
        locale="en",
    )


def ADMIN():
    return _user("admin", "admin")


def REV():
    return _user("rev", "reviewer")


def GROUPED():
    return _user("grouped", "reader", [G])


def OUTSIDER():
    return _user("outsider", "reader")


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
            ("grouped", "reader"),
            ("outsider", "reader"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                    " VALUES (CAST(:id AS uuid), :e, 'x', :r, CAST(:t AS uuid))"
                ),
                {"id": U[key], "e": f"{key}@example.com", "r": role, "t": TENANT["id"]},
            )
        await conn.execute(
            text(
                "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'HR')"
            ),
            {"g": G, "t": TENANT["id"]},
        )
        await ingest_router._ensure_table(
            conn,
            T,
            {
                "document_type": "x",
                "reference_number": "x",
                "subject": "x",
                "amount": "x",
            },
        )


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
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


def run(coro):
    return asyncio.run(coro)


def _ids(client, **params):
    r = client.get("/api/db/documents", params={"page_size": 100, **params})
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()["results"]}


# ── 3.1 tree ──────────────────────────────────────────────────────────────────


def test_build_the_tree(as_user, pg):
    admin = as_user(ADMIN())

    def create(name, parent=None, code=None, position=0):
        r = admin.post(
            "/api/db/series",
            json={
                "name": name,
                "parent_id": parent,
                "code": code,
                "position": position,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["id"]

    S["A"] = create("Administration", code="1", position=1)
    S["A1"] = create("Personnel", S["A"], code="1.1")
    S["A1a"] = create("Contracts", S["A1"], code="1.1.1")
    S["B"] = create("Finance", code="2", position=2)
    assert as_user(REV()).post("/api/db/series", json={"name": "x"}).status_code == 403
    # Names are unique among siblings only.
    assert (
        admin.post(
            "/api/db/series", json={"name": "Personnel", "parent_id": S["A"]}
        ).status_code
        == 409
    )
    S["B1"] = create("Personnel", S["B"])

    tree = admin.get("/api/db/series/tree").json()
    assert [n["name"] for n in tree] == ["Administration", "Finance"]
    a1 = tree[0]["children"][0]
    assert a1["code"] == "1.1" and a1["children"][0]["name"] == "Contracts"
    assert a1["children"][0]["path"] == f"/{S['A']}/{S['A1']}/{S['A1a']}/"

    for key, series in (("a1a", S["A1a"]), ("b", S["B"]), ("none", None)):
        run(
            _q(
                pg,
                f'INSERT INTO "{T}" (id, reference_number, subject, review_status, series_id)'
                " VALUES (CAST(:id AS uuid), :ref, :subj, 'approved', CAST(:s AS uuid))",
                {
                    "id": D[key],
                    "ref": f"REF-{key}",
                    "subj": f"subject {key}",
                    "s": series,
                },
            )
        )


def test_series_grant_reaches_the_whole_subtree(as_user):
    admin = as_user(ADMIN())
    r = admin.post(
        "/api/db/grants",
        json={
            "scope": "series",
            "series_id": S["A"],
            "grantee_type": "group",
            "grantee_id": G,
            "permission": "read",
        },
    )
    assert r.status_code == 200, r.text
    # The grant names "Administration"; the document sits two levels below.
    assert D["a1a"] in _ids(as_user(GROUPED()))
    assert _ids(as_user(OUTSIDER())) == {D["b"], D["none"]}
    assert (
        as_user(OUTSIDER()).get(f"/api/db/documents/{T}/{D['a1a']}").status_code == 404
    )
    # Counts never reveal what the caller can't see.
    counts = {
        s["id"]: s["document_count"]
        for s in as_user(OUTSIDER()).get("/api/db/series").json()
    }
    assert counts[S["A1a"]] == 0 and counts[S["B"]] == 1
    counts = {
        s["id"]: s["document_count"]
        for s in as_user(GROUPED()).get("/api/db/series").json()
    }
    assert counts[S["A1a"]] == 1


def test_move_rewrites_the_subtree_and_changes_who_sees_what(as_user, pg):
    admin = as_user(ADMIN())
    # A "Personnel" already exists under Finance: same-name siblings refused.
    assert (
        admin.patch(f"/api/db/series/{S['A1']}", json={"parent_id": S["B"]}).status_code
        == 409
    )
    assert (
        admin.patch(f"/api/db/series/{S['B1']}", json={"name": "Payroll"}).status_code
        == 200
    )
    r = admin.patch(f"/api/db/series/{S['A1']}", json={"parent_id": S["B"]})
    assert r.status_code == 200, r.text
    paths = {
        str(row["id"]): row["path"]
        for row in run(_q(pg, "SELECT id, path FROM sdai_series"))
    }
    assert paths[S["A1"]] == f"/{S['B']}/{S['A1']}/"
    assert paths[S["A1a"]] == f"/{S['B']}/{S['A1']}/{S['A1a']}/"
    # Out of the restricted "Administration": open to the tenant again.
    assert D["a1a"] in _ids(as_user(OUTSIDER()))
    # Move back for the following tests.
    assert (
        admin.patch(f"/api/db/series/{S['A1']}", json={"parent_id": S["A"]}).status_code
        == 200
    )
    assert D["a1a"] not in _ids(as_user(OUTSIDER()))


def test_moves_that_would_make_a_cycle_are_refused(as_user):
    admin = as_user(ADMIN())
    for target in (S["A"], S["A1"], S["A1a"]):
        r = admin.patch(f"/api/db/series/{S['A']}", json={"parent_id": target})
        assert r.status_code == 409, target
    assert (
        admin.patch(
            f"/api/db/series/{S['A1']}", json={"parent_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    # Rename / recode / reorder without moving.
    r = admin.patch(
        f"/api/db/series/{S['B']}",
        json={"name": "Finances", "code": "2", "position": 0},
    )
    assert r.status_code == 200 and r.json()["name"] == "Finances"
    tree = admin.get("/api/db/series/tree").json()
    assert [n["name"] for n in tree] == ["Finances", "Administration"]


def test_only_empty_series_can_be_deleted(as_user):
    admin = as_user(ADMIN())
    assert admin.delete(f"/api/db/series/{S['A']}").status_code == 409  # has sub-series
    r = admin.delete(f"/api/db/series/{S['A1a']}")
    assert (
        r.status_code == 409 and "1 documents" in r.json()["detail"]
    )  # has a document
    leaf = admin.post(
        "/api/db/series", json={"name": "Empty", "parent_id": S["A1a"]}
    ).json()["id"]
    assert admin.delete(f"/api/db/series/{leaf}").status_code == 200
    assert as_user(REV()).delete(f"/api/db/series/{S['B']}").status_code == 403


# ── 3.2 subtree filter ────────────────────────────────────────────────────────


def test_series_filter_with_and_without_subseries(as_user):
    admin = as_user(ADMIN())
    assert _ids(admin, series=S["A"]) == set()
    assert _ids(admin, series=S["A"], include_subseries="true") == {D["a1a"]}
    assert _ids(admin, series=S["A1a"]) == {D["a1a"]}
    assert _ids(admin, series=S["B"], include_subseries="true") == {D["b"]}
    # Searching inside a subtree combines with full-text search.
    assert _ids(admin, series=S["A"], include_subseries="true", q="subject") == {
        D["a1a"]
    }
    # The filter never widens access: the outsider still sees nothing there.
    assert _ids(as_user(OUTSIDER()), series=S["A"], include_subseries="true") == set()
    assert _ids(as_user(GROUPED()), series=S["A"], include_subseries="true") == {
        D["a1a"]
    }
    r = admin.get("/api/db/documents", params={"series": "not-a-uuid"})
    assert r.status_code == 422


# ── 3.3 tags ──────────────────────────────────────────────────────────────────

TAGS: dict = {}


def test_tagging_needs_edit_and_creates_tags_by_name(as_user):
    rev, admin = as_user(REV()), as_user(ADMIN())
    # The reviewer can't see the restricted document: can't tag it either.
    url = f"/api/db/documents/{T}/{D['a1a']}/tags"
    assert rev.post(url, json={"name": "urgent"}).status_code == 404
    assert (
        as_user(GROUPED()).post(url, json={"name": "urgent"}).status_code == 403
    )  # reader

    r = admin.post(url, json={"name": "Urgent"})
    assert r.status_code == 200, r.text
    TAGS["urgent"] = r.json()[0]["id"]
    # Same name, other case: the existing tag, not a second one.
    r = rev.post(f"/api/db/documents/{T}/{D['b']}/tags", json={"name": "urgent"})
    assert [t["id"] for t in r.json()] == [TAGS["urgent"]]
    r = rev.post(f"/api/db/documents/{T}/{D['b']}/tags", json={"name": "budget 2026"})
    TAGS["budget"] = next(t["id"] for t in r.json() if t["name"] == "budget 2026")
    # Twice is harmless.
    assert (
        len(
            rev.post(
                f"/api/db/documents/{T}/{D['b']}/tags", json={"tag_id": TAGS["budget"]}
            ).json()
        )
        == 2
    )
    assert rev.post(f"/api/db/documents/{T}/{D['b']}/tags", json={}).status_code == 422
    assert (
        as_user(OUTSIDER())
        .get(f"/api/db/documents/{T}/{D['b']}/tags")
        .json()[0]["name"]
        == "budget 2026"
    )


def test_tag_counts_and_filter_respect_access(as_user):
    counts = {
        t["name"]: t["document_count"]
        for t in as_user(OUTSIDER()).get("/api/db/tags").json()
    }
    assert counts == {
        "budget 2026": 1,
        "Urgent": 1,
    }  # D['a1a'] is hidden from the outsider
    counts = {
        t["name"]: t["document_count"]
        for t in as_user(ADMIN()).get("/api/db/tags").json()
    }
    assert counts["Urgent"] == 2
    assert _ids(as_user(OUTSIDER()), tag=TAGS["urgent"]) == {D["b"]}
    assert _ids(as_user(GROUPED()), tag=TAGS["urgent"]) == {D["a1a"], D["b"]}
    assert _ids(
        as_user(ADMIN()), tag=TAGS["urgent"], series=S["A"], include_subseries="true"
    ) == {D["a1a"]}


def test_tag_management_is_admin_only(as_user):
    rev, admin = as_user(REV()), as_user(ADMIN())
    assert (
        rev.patch(f"/api/db/tags/{TAGS['budget']}", json={"name": "x"}).status_code
        == 403
    )
    assert rev.delete(f"/api/db/tags/{TAGS['budget']}").status_code == 403
    assert (
        admin.patch(
            f"/api/db/tags/{TAGS['budget']}", json={"name": "URGENT"}
        ).status_code
        == 409
    )
    r = admin.patch(
        f"/api/db/tags/{TAGS['budget']}", json={"name": "Budget", "color": "#1d4ed8"}
    )
    assert r.json()["color"] == "#1d4ed8"
    assert (
        admin.patch(
            f"/api/db/tags/{TAGS['budget']}", json={"color": "blue"}
        ).status_code
        == 422
    )
    assert admin.post("/api/db/tags", json={"name": "budget"}).status_code == 409

    # Merge "Budget" into "Urgent": D['b'] had both - it keeps one.
    r = admin.post(
        f"/api/db/tags/{TAGS['budget']}/merge", json={"into_id": TAGS["urgent"]}
    )
    assert r.status_code == 200, r.text
    assert [
        t["name"] for t in admin.get(f"/api/db/documents/{T}/{D['b']}/tags").json()
    ] == ["Urgent"]
    assert [t["name"] for t in admin.get("/api/db/tags").json()] == ["Urgent"]

    r = rev.delete(f"/api/db/documents/{T}/{D['b']}/tags/{TAGS['urgent']}")
    assert r.status_code == 200 and r.json() == []
    assert admin.delete(f"/api/db/tags/{TAGS['urgent']}").status_code == 200
    assert admin.get(f"/api/db/documents/{T}/{D['a1a']}/tags").json() == []


# ── 3.4 automatic filing ──────────────────────────────────────────────────────


def _file(ref: str) -> str | None:
    import ingest_router

    async def go():
        await ingest_router._store_extraction(
            None,
            f"/img/{ref}.png",
            {"document_type": "invoice", "reference_number": ref},
            "high",
            "approved",
            TENANT["id"],
            "default",
            content_hash=f"hash-{ref}",
        )
        rows = await _q(
            ingest_router.engine,
            f'SELECT series_id::text AS s FROM "{T}" WHERE reference_number = :r',
            {"r": ref},
        )
        return rows[0]["s"]

    return run(go())


def test_documents_without_a_rule_land_in_to_be_filed(as_user, pg):
    first, second = _file("AUTO-1"), _file("AUTO-2")
    rows = run(
        _q(
            pg,
            "SELECT id::text AS id, name, system_key FROM sdai_series WHERE system_key IS NOT NULL",
        )
    )
    assert rows == [{"id": first, "name": "À classer", "system_key": "to_be_filed"}]
    assert second == first
    rules = as_user(ADMIN()).get("/api/db/filing-rules").json()
    assert rules["to_be_filed_series_id"] == first
    assert rules["rules"] == [{"table_name": T, "series_id": None}]


def test_filing_rule_files_new_documents_of_the_type(as_user):
    admin, rev = as_user(ADMIN()), as_user(REV())
    assert (
        rev.put(f"/api/db/filing-rules/{T}", json={"series_id": S["B"]}).status_code
        == 403
    )
    assert (
        admin.put(
            "/api/db/filing-rules/t_default_nope", json={"series_id": S["B"]}
        ).status_code
        == 404
    )
    assert (
        admin.put(
            f"/api/db/filing-rules/{T}", json={"series_id": str(uuid.uuid4())}
        ).status_code
        == 404
    )
    assert (
        admin.put(f"/api/db/filing-rules/{T}", json={"series_id": S["B"]}).status_code
        == 200
    )
    assert _file("AUTO-3") == S["B"]
    # A series with a filing rule is not empty.
    assert admin.delete(f"/api/db/series/{S['B']}").status_code == 409
    assert (
        admin.put(f"/api/db/filing-rules/{T}", json={"series_id": None}).status_code
        == 200
    )
    assert (
        _file("AUTO-4")
        == admin.get("/api/db/filing-rules").json()["to_be_filed_series_id"]
    )


# ── 3.5 link suggestions ──────────────────────────────────────────────────────

L = {k: str(uuid.uuid4()) for k in ("title", "sale", "secret_sale", "noise")}


def test_suggestions_in_both_directions_respecting_access(as_user, pg):
    for key, ref, subject, series in (
        ("title", "N°123/MIN/2026", "titre foncier", None),
        ("sale", "VENTE-77", "vente suite au titre N°123/MIN/2026", None),
        # Same mention, but filed in the restricted "Administration" subtree.
        ("secret_sale", "VENTE-78", "cession du titre N°123/MIN/2026", S["A1a"]),
        ("noise", "AUTRE-1", "sans rapport", None),
    ):
        run(
            _q(
                pg,
                f'INSERT INTO "{T}" (id, reference_number, subject, review_status, series_id)'
                " VALUES (CAST(:id AS uuid), :ref, :subj, 'approved', CAST(:s AS uuid))",
                {"id": L[key], "ref": ref, "subj": subject, "s": series},
            )
        )

    def suggest(user, key):
        r = as_user(user).get(f"/api/db/documents/{T}/{L[key]}/link-suggestions")
        assert r.status_code == 200, r.text
        return {(s["id"], s["direction"]) for s in r.json()}

    # The sale mentions the title's reference.
    assert (L["title"], "mentions") in suggest(REV(), "sale")
    # The title is mentioned by both sales - the outsider only sees the open one.
    assert suggest(ADMIN(), "title") == {
        (L["sale"], "mentioned_by"),
        (L["secret_sale"], "mentioned_by"),
    }
    assert suggest(OUTSIDER(), "title") == {(L["sale"], "mentioned_by")}
    assert suggest(ADMIN(), "noise") == set()
    # Hidden source document: no suggestions at all.
    assert (
        as_user(OUTSIDER())
        .get(f"/api/db/documents/{T}/{L['secret_sale']}/link-suggestions")
        .status_code
        == 404
    )

    # Once linked (existing endpoint), no longer suggested - either way round.
    r = as_user(ADMIN()).post(
        f"/api/db/documents/{T}/{L['sale']}/links",
        json={"to_table": T, "to_id": L["title"], "relation": "concerne"},
    )
    assert r.status_code == 200, r.text
    assert (L["title"], "mentions") not in suggest(ADMIN(), "sale")
    assert (L["sale"], "mentioned_by") not in suggest(ADMIN(), "title")
