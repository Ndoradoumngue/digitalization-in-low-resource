"""Access control against a real PostgreSQL (GED Phase 1 leak tests).

The mocked suite only checks the SQL text; this one runs it. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_access_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
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

TI = "t_default_invoice"
TC = "t_default_contract"


def _id() -> str:
    return str(uuid.uuid4())


# Users (ids also become sdai_users rows so grantee checks pass)
U = {
    k: _id() for k in ("admin", "rev", "rev2", "reader", "reader_conf", "other_tenant")
}
G = _id()  # group the reader belongs to
S1 = _id()  # series restricted to G (read)
S2 = _id()  # unrestricted series

D = {
    k: _id()
    for k in (
        "open",
        "private",
        "series",
        "conf",
        "trash",
        "review",
        "contract",
        "orphan",
    )
}
BD = {k: _id() for k in ("open", "private", "pipeline")}


def _user(
    key, role, *, clearance="internal", groups=(), tenant=None, **flags
) -> CurrentUser:
    tid, slug = tenant or (TENANT["id"], "default")
    return CurrentUser(
        id=U[key],
        email=f"{key}@example.com",
        full_name=key,
        role=role,
        tenant_id=tid,
        tenant_slug=slug,
        tenant_name=slug,
        can_manage_access=flags.get("can_manage_access", False),
        can_edit_extraction=flags.get("can_edit_extraction", False),
        group_ids=list(groups),
        locale="en",
        clearance=clearance,
    )


TENANT: dict = {}
FILES: dict = {}


async def _seed(engine, files_dir):
    import ingest_router
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    # Real startup DDL, twice - it must be idempotent.
    await ingest_router._init_db()
    await ingest_router._init_db()

    async with engine.begin() as conn:
        TENANT["id"] = str(
            (
                await conn.execute(
                    text("SELECT id FROM sdai_tenants WHERE slug = 'default'")
                )
            ).scalar()
        )
        other_tid = (
            await conn.execute(
                text(
                    "INSERT INTO sdai_tenants (slug, name) VALUES ('other', 'Other') RETURNING id"
                )
            )
        ).scalar()
        TENANT["other"] = str(other_tid)
        for key, role in [
            ("admin", "admin"),
            ("rev", "reviewer"),
            ("rev2", "reviewer"),
            ("reader", "reader"),
            ("reader_conf", "reader"),
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
                "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
                " VALUES (CAST(:id AS uuid), 'o@example.com', 'x', 'reviewer', CAST(:t AS uuid))"
            ),
            {"id": U["other_tenant"], "t": TENANT["other"]},
        )
        await conn.execute(
            text(
                "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'HR')"
            ),
            {"g": G, "t": TENANT["id"]},
        )
        for sid, name in [(S1, "Restricted"), (S2, "Open")]:
            await conn.execute(
                text(
                    "INSERT INTO sdai_series (id, tenant_id, name) VALUES (CAST(:s AS uuid), CAST(:t AS uuid), :n)"
                ),
                {"s": sid, "t": TENANT["id"], "n": name},
            )

        fields = {"document_type": "x", "reference_number": "x"}
        await ingest_router._ensure_table(conn, TI, fields)
        await ingest_router._ensure_table(conn, TC, fields)

        # Pipeline bookkeeping: one batch with three uploads, each with a page image.
        batch = (
            await conn.execute(
                text(
                    "INSERT INTO batches (source_type, tenant_id) VALUES ('upload', CAST(:t AS uuid)) RETURNING id"
                ),
                {"t": TENANT["id"]},
            )
        ).scalar()
        for key in BD:
            img = files_dir / f"{key}.png"
            img.write_bytes(b"\x89PNG")
            FILES[key] = str(img)
            await conn.execute(
                text(
                    "INSERT INTO batch_documents (id, batch_id, filename, image_path, source_path)"
                    " VALUES (CAST(:id AS uuid), :b, :f, :p, :p)"
                ),
                {"id": BD[key], "b": batch, "f": f"{key}.png", "p": str(img)},
            )
            await conn.execute(
                text(
                    "INSERT INTO batch_document_pages (batch_document_id, page_number, image_path)"
                    " VALUES (CAST(:bd AS uuid), 1, :p)"
                ),
                {"bd": BD[key], "p": str(img)},
            )
        orphan = files_dir / "orphan.png"
        orphan.write_bytes(b"\x89PNG")
        FILES["orphan"] = str(orphan)

        async def doc(
            table,
            key,
            ref,
            *,
            status="approved",
            conf="internal",
            series=None,
            trashed=False,
            bd=None,
            image=None,
        ):
            await conn.execute(
                text(
                    f'INSERT INTO "{table}" (id, source_image_path, document_type, reference_number,'
                    " review_status, confidentiality, series_id, deleted_at, batch_document_id)"
                    " VALUES (CAST(:id AS uuid), :img, 'x', :ref, :st, :conf, CAST(:s AS uuid),"
                    "         CASE WHEN :tr THEN now() END, CAST(:bd AS uuid))"
                ),
                {
                    "id": D[key],
                    "img": image or f"/nowhere/{key}.png",
                    "ref": ref,
                    "st": status,
                    "conf": conf,
                    "s": series,
                    "tr": trashed,
                    "bd": bd,
                },
            )

        await doc(
            TI, "open", "alpha open", series=S2, bd=BD["open"], image=FILES["open"]
        )
        await doc(
            TI, "private", "bravo secret", bd=BD["private"], image=FILES["private"]
        )
        await doc(TI, "series", "charlie filed", series=S1)
        await doc(TI, "conf", "delta classified", conf="confidential")
        await doc(TI, "trash", "echo trashed", trashed=True)
        await doc(TI, "review", "foxtrot pending", status="review_required")
        await doc(TC, "contract", "golf contract")
        await doc(TI, "orphan", "hotel legacy", image=FILES["orphan"])

        async def grant(
            scope,
            grantee_type,
            grantee,
            permission,
            *,
            table=None,
            doc_id=None,
            series=None,
        ):
            await conn.execute(
                text(
                    "INSERT INTO sdai_document_access (tenant_id, scope, table_name, document_id,"
                    " series_id, grantee_type, grantee_id, permission)"
                    " VALUES (CAST(:t AS uuid), :sc, :tb, CAST(:d AS uuid), CAST(:s AS uuid), :gt,"
                    "         CAST(:g AS uuid), :p)"
                ),
                {
                    "t": TENANT["id"],
                    "sc": scope,
                    "tb": table,
                    "d": doc_id,
                    "s": series,
                    "gt": grantee_type,
                    "g": grantee,
                    "p": permission,
                },
            )

        await grant(
            "document", "user", U["rev2"], "edit", table=TI, doc_id=D["private"]
        )
        await grant("series", "group", G, "read", series=S1)
        await grant("doc_type", "user", U["reader"], "download", table=TC)

        await conn.execute(
            text(
                "INSERT INTO sdai_document_links (tenant_id, from_table, from_id, to_table, to_id, relation, note)"
                " VALUES (CAST(:t AS uuid), :ft, CAST(:f AS uuid), :tt, CAST(:to AS uuid), 'concerns', 'about bravo')"
            ),
            {"t": TENANT["id"], "ft": TI, "f": D["open"], "tt": TI, "to": D["private"]},
        )


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import documents_router
    import ingest_router
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    files_dir = tmp_path_factory.mktemp("files")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    saved = (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [files_dir.resolve()]
    asyncio.run(_seed(engine, files_dir))
    yield
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
    ) = saved


class _As:
    """A client acting as one user. Dependency overrides are app-wide, so
    the override is (re)installed right before every request."""

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


def ADMIN():
    return _user("admin", "admin")


def REV():
    return _user("rev", "reviewer", can_edit_extraction=True)


def REV2():
    return _user("rev2", "reviewer")


def READER():
    return _user("reader", "reader", groups=[G])


def READER_CONF():
    return _user("reader_conf", "reader", clearance="confidential")


def _ids(client, **params) -> set[str]:
    resp = client.get("/api/db/documents", params={"page_size": 100, **params})
    assert resp.status_code == 200, resp.text
    return {r["id"] for r in resp.json()["results"]}


def names(*keys) -> set[str]:
    return {D[k] for k in keys}


# ── Listing / search ──────────────────────────────────────────────────────────


def test_list_per_role(as_user):
    assert _ids(as_user(ADMIN())) == names(
        "open", "private", "series", "conf", "review", "contract", "orphan"
    )
    assert _ids(as_user(REV())) == names("open", "review", "orphan")
    assert _ids(as_user(REV2())) == names("open", "private", "review", "orphan")
    assert _ids(as_user(READER())) == names(
        "open", "series", "review", "contract", "orphan"
    )
    assert _ids(as_user(READER_CONF())) == names("open", "conf", "review", "orphan")


def test_trashed_hidden_even_for_admin(as_user):
    assert D["trash"] not in _ids(as_user(ADMIN()))


def test_search_never_matches_invisible(as_user):
    assert _ids(as_user(REV()), q="bravo") == set()
    assert _ids(as_user(REV2()), q="bravo") == names("private")
    assert _ids(as_user(REV()), q="delta") == set()
    assert _ids(as_user(READER_CONF()), q="delta") == names("conf")


def test_series_filter_respects_series_grant(as_user):
    assert _ids(as_user(REV()), series=S1) == set()
    assert _ids(as_user(READER()), series=S1) == names("series")


def test_type_counts(as_user):
    counts = {
        r["table_name"]: r["count"] for r in as_user(REV()).get("/api/db/types").json()
    }
    assert counts == {TI: 3, TC: 0}
    counts = {
        r["table_name"]: r["count"]
        for r in as_user(READER()).get("/api/db/types").json()
    }
    assert counts == {TI: 4, TC: 1}


def test_schema_counts(as_user):
    tables = {
        t["name"]: t["row_count"]
        for t in as_user(REV()).get("/api/db/schema").json()["tables"]
    }
    assert tables == {TI: 3, TC: 0}


# ── Detail / export / permission levels ───────────────────────────────────────


def test_detail_404_when_invisible(as_user):
    rev = as_user(REV())
    assert rev.get(f"/api/db/documents/{TI}/{D['private']}").status_code == 404
    assert rev.get(f"/api/db/documents/{TI}/{D['conf']}").status_code == 404
    assert rev.get(f"/api/db/documents/{TC}/{D['contract']}").status_code == 404
    assert rev.get(f"/api/db/documents/{TI}/{D['open']}").status_code == 200


def test_export_needs_download(as_user):
    reader = as_user(READER())
    # series grant is read-only -> visible but not exportable
    assert reader.get(f"/api/db/documents/{TI}/{D['series']}").status_code == 200
    assert reader.get(f"/api/db/documents/{TI}/{D['series']}/export").status_code == 403
    # type grant is download
    assert (
        reader.get(f"/api/db/documents/{TC}/{D['contract']}/export").status_code == 200
    )
    # open document, reader role caps at download
    assert reader.get(f"/api/db/documents/{TI}/{D['open']}/export").status_code == 200
    assert (
        as_user(REV()).get(f"/api/db/documents/{TI}/{D['private']}/export").status_code
        == 404
    )


def test_effective_permission(as_user):
    def perm(client, table, key):
        return client.get(f"/api/db/documents/{table}/{key}/access").json()[
            "my_permission"
        ]

    assert perm(as_user(READER()), TI, D["series"]) == "read"
    assert perm(as_user(READER()), TC, D["contract"]) == "download"
    assert perm(as_user(READER()), TI, D["open"]) == "download"
    assert perm(as_user(REV()), TI, D["open"]) == "edit"
    assert perm(as_user(REV2()), TI, D["private"]) == "edit"
    assert perm(as_user(ADMIN()), TI, D["private"]) == "edit"


def test_access_listing_shows_inherited_grants(as_user):
    grants = (
        as_user(READER())
        .get(f"/api/db/documents/{TI}/{D['series']}/access")
        .json()["grants"]
    )
    assert [(g["scope"], g["series_name"], g["permission"]) for g in grants] == [
        ("series", "Restricted", "read")
    ]


def test_field_edit_needs_edit_grant(as_user):
    rev = as_user(REV())
    ok = rev.patch(
        f"/api/db/documents/{TI}/{D['open']}/fields",
        json={"fields": {"reference_number": "alpha open"}},
    )
    assert ok.status_code == 200
    assert (
        rev.patch(
            f"/api/db/documents/{TI}/{D['private']}/fields",
            json={"fields": {"reference_number": "z"}},
        ).status_code
        == 404
    )


# ── Review queue / count ──────────────────────────────────────────────────────


def test_review_queue_and_count(as_user):
    rev = as_user(REV())
    assert {i["id"] for i in rev.get("/api/review/queue").json()["items"]} == names(
        "review"
    )
    assert rev.get("/api/review/count").json()["pending"] == 1
    reader = as_user(READER())
    assert reader.get("/api/review/queue").json()["items"] == []
    assert reader.get("/api/review/count").json()["pending"] == 0


# ── Links ─────────────────────────────────────────────────────────────────────


def test_link_to_invisible_document_is_anonymous(as_user):
    links = (
        as_user(REV()).get(f"/api/db/documents/{TI}/{D['open']}/links").json()["links"]
    )
    assert len(links) == 1
    assert links[0]["other_missing"] is True
    assert links[0]["other_id"] == "" and links[0]["note"] is None
    links = (
        as_user(REV2()).get(f"/api/db/documents/{TI}/{D['open']}/links").json()["links"]
    )
    assert links[0]["other_id"] == D["private"] and links[0]["note"] == "about bravo"


def test_reviewers_list_runs(as_user):
    assert as_user(READER()).get("/api/db/reviewers").status_code == 200


# ── Images ────────────────────────────────────────────────────────────────────


def _img(client, key, **params):
    return client.get(
        "/api/db/image", params={"path": FILES[key], **params}
    ).status_code


def test_image_follows_document_access(as_user):
    rev, rev2, reader = as_user(REV()), as_user(REV2()), as_user(READER())
    assert _img(rev, "open") == 200
    assert _img(rev, "private") == 404
    assert _img(rev2, "private") == 200
    assert _img(rev, "private", table=TI, id=D["private"]) == 404
    assert _img(rev2, "private", table=TI, id=D["private"]) == 200
    # a hint pointing at a visible document that does not own the file doesn't help
    assert _img(rev, "private", table=TI, id=D["open"]) == 404
    # still in the pipeline: contributors only
    assert _img(rev, "pipeline") == 200
    assert _img(reader, "pipeline") == 404
    # file no upload knows: allowed only through a readable document referencing it
    assert _img(reader, "orphan") == 200
    assert _img(as_user(_user("rev", "reviewer", clearance="public")), "orphan") == 404


def test_image_never_crosses_tenants(as_user):
    other = as_user(
        _user("other_tenant", "reviewer", tenant=(TENANT.get("other"), "other"))
    )
    assert _img(other, "open") == 404
    assert _img(other, "pipeline") == 404


# ── Ingestion pages ───────────────────────────────────────────────────────────


def test_pages_follow_filed_document(as_user):
    assert as_user(REV()).get(f"/api/ingest/pages/{BD['private']}").status_code == 404
    assert as_user(REV2()).get(f"/api/ingest/pages/{BD['private']}").status_code == 200
    assert as_user(REV()).get(f"/api/ingest/pages/{BD['pipeline']}").status_code == 200
    assert as_user(READER()).get(f"/api/ingest/pages/{BD['open']}").status_code == 403


# ── Grants, scoped grants, confidentiality (writes) ──────────────────────────


def test_scoped_grant_lifecycle(as_user):
    admin = as_user(ADMIN())
    rev = as_user(REV())
    assert D["open"] in _ids(rev)
    # restrict the open series S2 to rev2 only -> rev loses D_open
    resp = admin.post(
        "/api/db/grants",
        json={
            "scope": "series",
            "series_id": S2,
            "grantee_type": "user",
            "grantee_id": U["rev2"],
        },
    )
    assert resp.status_code == 200, resp.text
    gid = resp.json()["id"]
    # re-granting the same grantee upserts the level instead of failing
    again = admin.post(
        "/api/db/grants",
        json={
            "scope": "series",
            "series_id": S2,
            "grantee_type": "user",
            "grantee_id": U["rev2"],
            "permission": "edit",
        },
    )
    assert again.json()["id"] == gid
    assert D["open"] not in _ids(rev)
    listed = admin.get("/api/db/grants", params={"scope": "series"}).json()["grants"]
    assert {(g["series_id"], g["permission"]) for g in listed} >= {(S2, "edit")}
    assert admin.delete(f"/api/db/access/{gid}").status_code == 200
    assert D["open"] in _ids(rev)


def test_document_grant_upsert_and_level_change(as_user):
    admin = as_user(ADMIN())
    url = f"/api/db/documents/{TI}/{D['review']}/access"
    g1 = admin.post(
        url, json={"grantee_type": "user", "grantee_id": U["reader"]}
    ).json()["id"]
    g2 = admin.post(
        url,
        json={
            "grantee_type": "user",
            "grantee_id": U["reader"],
            "permission": "download",
        },
    ).json()["id"]
    assert g1 == g2
    grants = admin.get(url).json()["grants"]
    assert [(g["scope"], g["permission"]) for g in grants] == [("document", "download")]
    assert (
        admin.patch(f"/api/db/access/{g1}", json={"permission": "read"}).status_code
        == 200
    )
    assert admin.get(url).json()["grants"][0]["permission"] == "read"
    # the document is now restricted: rev lost it from the queue
    assert as_user(REV()).get("/api/review/count").json()["pending"] == 0
    assert admin.delete(f"/api/db/access/{g1}").status_code == 200
    assert as_user(REV()).get("/api/review/count").json()["pending"] == 1


def test_confidentiality_change(as_user):
    admin = as_user(ADMIN())
    url = f"/api/db/documents/{TI}/{D['open']}/confidentiality"
    assert admin.patch(url, json={"confidentiality": "secret"}).status_code == 200
    assert D["open"] not in _ids(as_user(REV()))
    assert D["open"] not in _ids(as_user(READER_CONF()))
    assert admin.patch(url, json={"confidentiality": "internal"}).status_code == 200
    assert D["open"] in _ids(as_user(REV()))


def test_scope_check_constraint_rejects_inconsistent_rows(pg):
    import ingest_router
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async def bad():
        async with ingest_router.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sdai_document_access (tenant_id, scope, table_name, grantee_type, grantee_id)"
                    " VALUES (CAST(:t AS uuid), 'document', :tb, 'user', CAST(:u AS uuid))"
                ),
                {"t": TENANT["id"], "tb": TI, "u": U["rev"]},
            )

    with pytest.raises(IntegrityError):
        asyncio.run(bad())


def test_role_check_accepts_reader_only(pg):
    import ingest_router
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async def set_role(role):
        async with ingest_router.engine.begin() as conn:
            await conn.execute(
                text("UPDATE sdai_users SET role = :r WHERE id = CAST(:id AS uuid)"),
                {"r": role, "id": U["reader"]},
            )

    asyncio.run(set_role("reader"))
    with pytest.raises(IntegrityError):
        asyncio.run(set_role("superuser"))


# ── The headline leak test ────────────────────────────────────────────────────


def test_reader_without_grant_leaks_nothing(as_user):
    """A reader holding no grant (READER_CONF is in no group and named by no
    grant) sees none of the restricted documents through any surface."""
    r = as_user(_user("reader_conf", "reader"))  # internal clearance, no grants
    hidden = {"private": TI, "series": TI, "contract": TC, "conf": TI}
    listed = _ids(r)
    for key, table in hidden.items():
        assert D[key] not in listed, key
        assert r.get(f"/api/db/documents/{table}/{D[key]}").status_code == 404, key
        assert r.get(f"/api/db/documents/{table}/{D[key]}/export").status_code == 404, (
            key
        )
        assert r.get(f"/api/db/documents/{table}/{D[key]}/links").status_code == 404, (
            key
        )
        assert r.get(f"/api/db/documents/{table}/{D[key]}/access").status_code == 404, (
            key
        )
    for word in ("bravo", "charlie", "golf", "delta"):
        assert _ids(r, q=word) == set(), word
    assert _ids(r, document_type=TC) == set()
    assert r.get("/api/review/queue").json() == {"total": 0, "items": []}
    assert r.get("/api/review/count").json() == {"pending": 0}
    counts = {t["table_name"]: t["count"] for t in r.get("/api/db/types").json()}
    assert counts[TC] == 0
    links = r.get(f"/api/db/documents/{TI}/{D['open']}/links").json()["links"]
    assert links[0]["other_missing"] and links[0]["other_id"] == ""
    assert _img(r, "private") == 404
    assert _img(r, "private", table=TI, id=D["private"]) == 404
