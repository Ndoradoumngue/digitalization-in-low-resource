"""Two nodes, two real PostgreSQL databases (GED Phase 8): a 'local' node
syncing with a 'cloud' node through the same code path the sync worker
uses, the transport being in-process (serve_* = what the /api/sync
handlers run) instead of HTTP. Opt-in:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_sync_twonode_pg.py -m pg

The peer database is TEST_PEER_DATABASE_URL, else <empty_db>_peer on the
same server (created if missing). Both are wiped - never real data.
"""

import asyncio
import contextlib
import json
import os
import uuid

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

T = "t_default_invoice"
DATA: dict = {}


def _id() -> str:
    return str(uuid.uuid4())


def _peer_url() -> str:
    if os.getenv("TEST_PEER_DATABASE_URL"):
        return os.environ["TEST_PEER_DATABASE_URL"]
    base, _, db = TEST_DATABASE_URL.rpartition("/")
    return f"{base}/{db.split('?')[0]}_peer"


class Node:
    def __init__(self, name: str, role: str, url: str):
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        self.name, self.role = name, role
        self.engine = create_async_engine(url, poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        def stamp(dbapi_connection, _record):
            cur = dbapi_connection.cursor()
            cur.execute(f"SET sdai.node_id = '{name}'")
            cur.close()

        event.listen(self.engine.sync_engine, "connect", stamp)

    @contextlib.contextmanager
    def active(self):
        """Make the module-level state (engine, NODE_ID) this node's, as it
        is inside that node's own processes."""
        import ingest_router

        from sync import files as sync_files

        saved_chroot = sync_files.CHROOT
        sync_files.CHROOT = getattr(self, "disk", None)
        saved = (
            ingest_router.engine,
            ingest_router.SessionLocal,
            ingest_router.NODE_ID,
            ingest_router.NODE_ROLE,
        )
        ingest_router.engine, ingest_router.SessionLocal = self.engine, self.sessions
        ingest_router.NODE_ID, ingest_router.NODE_ROLE = self.name, self.role
        try:
            yield self
        finally:
            (
                ingest_router.engine,
                ingest_router.SessionLocal,
                ingest_router.NODE_ID,
                ingest_router.NODE_ROLE,
            ) = saved
            sync_files.CHROOT = saved_chroot

    async def q(self, sql, params=None):
        from sqlalchemy import text

        async with self.engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return (
                [dict(r) for r in result.mappings().all()]
                if result.returns_rows
                else []
            )

    async def one(self, sql, params=None):
        rows = await self.q(sql, params)
        return rows[0] if rows else None


def _wire(payload):
    """What survives the JSON trip over HTTP."""
    return json.loads(json.dumps(payload, default=str))


class InProcessPeer:
    """The worker's view of `server`, as seen from `caller`."""

    def __init__(self, server: Node, caller: Node):
        self.server, self.caller = server, caller
        self.push_responses: list[dict] = []

    async def hello(self):
        from sync.store import serve_hello

        with self.server.active():
            async with self.server.engine.begin() as conn:
                return _wire(await serve_hello(conn, self.caller.name))

    async def push(self, epoch, changes):
        from sync.store import after_apply, serve_push

        with self.server.active():
            async with self.server.engine.begin() as conn:
                response, result = await serve_push(
                    conn, self.caller.name, epoch, _wire(changes)
                )
            # What the /api/sync/push handler does after commit.
            await after_apply(result, self.caller.name)
        self.push_responses.append(response)
        return _wire(response)

    async def pull(self, since, limit, epoch=None):
        from sync.store import serve_pull

        with self.server.active():
            async with self.server.engine.begin() as conn:
                return _wire(
                    await serve_pull(conn, self.caller.name, since, limit, epoch)
                )

    # Files: the same primitives the /api/sync/files routes call, on the
    # server's disk; FileSyncError becomes the PeerError HttpPeer raises.
    async def _files(self, fn, *args):
        from sync import files
        from sync.worker import PeerError

        with self.server.active():
            try:
                return fn(*args)
            except files.FileSyncError as exc:
                raise PeerError(exc.status, str(exc.detail)) from exc

    async def file_info(self, path):
        from sync import files

        return await self._files(files.info, path)

    async def upload_chunk(self, path, offset, size, sha256, chunk):
        from sync import files

        return await self._files(files.write_chunk, path, offset, size, sha256, chunk)

    async def download_chunk(self, path, offset, length):
        from sync import files

        return await self._files(files.read_chunk, path, offset, length)


async def _reset(node: Node):
    import ingest_router
    from sqlalchemy import text

    async with node.engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    with node.active():
        await ingest_router._init_db()


async def _create_peer_db():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    db = _peer_url().rpartition("/")[2].split("?")[0]
    admin = create_async_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": db}
            )
        ).scalar()
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db}"'))
    await admin.dispose()


async def _setup(local: Node, cloud: Node):
    import ingest_router
    from sync.join import prepare_fresh_node

    await _create_peer_db()
    await _reset(local)
    await _reset(cloud)
    async with cloud.engine.begin() as conn:
        await prepare_fresh_node(conn)
    # Startup on the joined node while it waits: no 'default' tenant again.
    with cloud.active():
        await ingest_router._init_db()

    DATA["tenant"] = (
        await local.one(
            "SELECT id::text AS id FROM sdai_tenants WHERE slug = 'default'"
        )
    )["id"]
    DATA["user"], DATA["doc"] = _id(), _id()
    await local.q(
        "INSERT INTO sdai_users (id, email, hashed_password, role, tenant_id)"
        " VALUES (CAST(:u AS uuid), 'admin@example.com', 'x', 'admin', CAST(:t AS uuid))",
        {"u": DATA["user"], "t": DATA["tenant"]},
    )
    with local.active():
        async with local.engine.begin() as conn:
            await ingest_router._ensure_table(
                conn, T, {"reference_number": "x", "amount": "x", "items": ["a"]}
            )
    await local.q(
        f'INSERT INTO "{T}" (id, reference_number, amount, items, record_id, review_status)'
        " VALUES (CAST(:d AS uuid), 'REF-1', '100', '[\"a\"]', 'DEFAULT-1999-000001', 'approved')",
        {"d": DATA["doc"]},
    )
    await local.q(
        "INSERT INTO sdai_audit_log (user_id, user_email, action) VALUES (CAST(:u AS uuid), 'admin@example.com', 'login')",
        {"u": DATA["user"]},
    )


@pytest.fixture(scope="module")
def nodes(tmp_path_factory):
    local = Node("local", "local", TEST_DATABASE_URL)
    cloud = Node("cloud", "cloud", _peer_url())
    # Each node's own disk; files live at the same logical /data paths.
    local.disk = tmp_path_factory.mktemp("local-disk")
    cloud.disk = tmp_path_factory.mktemp("cloud-disk")
    asyncio.run(_setup(local, cloud))
    yield local, cloud


def run(coro):
    return asyncio.run(coro)


async def _sync(local: Node, cloud: Node) -> dict:
    from sync.worker import sync_once

    peer = InProcessPeer(cloud, local)
    # The worker runs as the local node (its audit entries land there).
    with local.active():
        summary = await sync_once(local.engine, peer)
    summary["push_conflicts"] = sum(r.get("conflicts", 0) for r in peer.push_responses)
    summary["push_errors"] = sum(r.get("errors", 0) for r in peer.push_responses)
    return summary


async def _doc(node: Node, doc_id: str, table: str = T):
    return await node.one(
        f'SELECT * FROM "{table}" WHERE id = CAST(:d AS uuid)', {"d": doc_id}
    )


# ── Tests (in order - each builds on the previous state) ─────────────────────


def test_joined_node_waits_without_default_tenant(nodes):
    _, cloud = nodes
    assert run(cloud.q("SELECT * FROM sdai_tenants")) == []
    assert (
        run(cloud.one("SELECT join_pending FROM sdai_sync_state"))["join_pending"]
        is True
    )


def test_first_sync_brings_the_whole_deployment(nodes):
    import ingest_router

    local, cloud = nodes
    summary = run(_sync(local, cloud))
    assert summary["peer"] == "cloud"
    assert summary["pushed"] > 0 and summary["push_errors"] == 0, summary

    tenant = run(cloud.one("SELECT id::text AS id, slug FROM sdai_tenants"))
    assert tenant == {"id": DATA["tenant"], "slug": "default"}
    assert (
        run(cloud.one("SELECT join_pending FROM sdai_sync_state"))["join_pending"]
        is False
    )
    user = run(
        cloud.one(
            "SELECT email, role FROM sdai_users WHERE id = CAST(:u AS uuid)",
            {"u": DATA["user"]},
        )
    )
    assert user == {"email": "admin@example.com", "role": "admin"}
    doc = run(_doc(cloud, DATA["doc"]))
    assert doc["reference_number"] == "REF-1" and doc["items"] == ["a"]
    assert doc["record_id"] == "DEFAULT-1999-000001" and doc["origin_node"] == "local"

    # The audit log arrived with its chain intact - verifiable on the cloud.
    with cloud.active():
        verdict = run(ingest_router._verify_audit_chain())
    assert verdict["ok"], verdict
    assert [c["origin_node"] for c in verdict["chains"]] == ["local"]

    # Nothing applied from the peer is captured again (no echo).
    assert run(cloud.one("SELECT count(*) AS n FROM sdai_sync_changelog"))["n"] == 0


def test_second_cycle_has_nothing_to_do(nodes):
    local, cloud = nodes
    summary = run(_sync(local, cloud))
    assert summary["pushed"] == 0 and summary["pulled"] == 0


def test_cloud_changes_come_back(nodes):
    local, cloud = nodes
    gid = _id()
    run(
        cloud.q(
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'Remote team')",
            {"g": gid, "t": DATA["tenant"]},
        )
    )
    run(
        cloud.q(
            f"UPDATE \"{T}\" SET review_status = 'pending' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    local_head = run(
        local.one("SELECT COALESCE(max(id), 0) AS m FROM sdai_sync_changelog")
    )["m"]

    summary = run(_sync(local, cloud))
    assert summary["pulled"] == 2 and summary["applied"] == 2 and summary["errors"] == 0
    group = run(
        local.one(
            "SELECT name, origin_node FROM sdai_groups WHERE id = CAST(:g AS uuid)",
            {"g": gid},
        )
    )
    assert group == {"name": "Remote team", "origin_node": "cloud"}
    assert run(_doc(local, DATA["doc"]))["review_status"] == "pending"
    # Applied, not captured: the local changelog did not grow.
    assert (
        run(local.one("SELECT COALESCE(max(id), 0) AS m FROM sdai_sync_changelog"))["m"]
        == local_head
    )


def test_edits_of_different_fields_merge(nodes):
    local, cloud = nodes
    run(
        local.q(
            f"UPDATE \"{T}\" SET reference_number = 'REF-LOCAL' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    run(
        cloud.q(
            f"UPDATE \"{T}\" SET amount = '200' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["conflicts"] == 0 and summary["push_conflicts"] == 0
    for node in nodes:
        doc = run(_doc(node, DATA["doc"]))
        assert (doc["reference_number"], doc["amount"]) == ("REF-LOCAL", "200"), (
            node.name
        )


def test_same_field_edited_on_both_sides_converges_and_is_reported(nodes):
    local, cloud = nodes
    run(
        local.q(
            f"UPDATE \"{T}\" SET amount = '300-local' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    # The cloud edit happens later - higher HLC, it wins everywhere.
    run(
        cloud.q(
            f"UPDATE \"{T}\" SET amount = '300-cloud' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["push_conflicts"] == 1 and summary["conflicts"] == 1, summary
    for node in nodes:
        assert run(_doc(node, DATA["doc"]))["amount"] == "300-cloud", node.name
    clocks = [
        run(
            node.one(
                "SELECT field_hlcs->>'amount' AS h FROM sdai_sync_row_clock WHERE table_name = :t AND pk LIKE :pk",
                {"t": T, "pk": f"%{DATA['doc']}%"},
            )
        )["h"]
        for node in nodes
    ]
    assert clocks[0] == clocks[1] and clocks[0].endswith(":cloud")
    # Next round: settled, nothing new.
    again = run(_sync(local, cloud))
    assert again["conflicts"] == 0 and again["push_conflicts"] == 0


def _admin():
    from auth import CurrentUser

    return CurrentUser(
        id=DATA["user"],
        email="admin@example.com",
        full_name="Admin",
        role="admin",
        tenant_id=DATA["tenant"],
        tenant_slug="default",
        tenant_name="Default",
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[],
        locale="fr",
    )


async def _conflicts(node, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "TRUE"
    return await node.q(
        f"SELECT * FROM sdai_sync_conflicts WHERE {clause} ORDER BY created_at", where
    )


def _queued(node):
    from review_router import _queue_where

    return [
        str(r["id"])
        for r in run(node.q(f'SELECT id FROM "{T}" WHERE {_queue_where(T)}'))
    ]


def test_conflict_recorded_on_both_nodes_with_history_queue_and_audit(nodes):
    import ingest_router

    local, cloud = nodes
    for node, value in ((local, "REF-L2"), (cloud, "REF-C2")):
        run(
            node.q(
                f'UPDATE "{T}" SET reference_number = :v WHERE id = CAST(:d AS uuid)',
                {"v": value, "d": DATA["doc"]},
            )
        )
    run(_sync(local, cloud))

    for node, winner in ((local, "remote"), (cloud, "local")):
        rows = run(_conflicts(node, field="reference_number", status="open"))
        assert len(rows) == 1, node.name
        c = rows[0]
        assert c["kind"] == "field" and c["winner"] == winner
        assert (
            str(c["document_id"]) == DATA["doc"]
            and str(c["tenant_id"]) == DATA["tenant"]
        )
        assert {c["local_value"], c["remote_value"]} == {"REF-L2", "REF-C2"}
        history = run(
            node.q(
                "SELECT old_value, new_value FROM sdai_field_history"
                " WHERE document_id = CAST(:d AS uuid) AND change_source = 'sync'"
                " AND field = 'reference_number'",
                {"d": DATA["doc"]},
            )
        )
        assert history == [{"old_value": "REF-L2", "new_value": "REF-C2"}], node.name
        # "Conflit de synchronisation": the document is back in the queue.
        assert _queued(node) == [DATA["doc"]], node.name

    # Each node audited its own detection; the next cycle carries the other
    # node's entry over - and every chain still verifies on both.
    run(_sync(local, cloud))
    for node in nodes:
        events = run(
            node.q(
                "SELECT origin_node FROM sdai_audit_log WHERE action = 'sync_conflict'"
                " AND details->>'field' = 'reference_number'"
            )
        )
        assert sorted(e["origin_node"] for e in events) == ["cloud", "local"], node.name
        with node.active():
            verdict = run(ingest_router._verify_audit_chain())
        assert verdict["ok"], (node.name, verdict)


def test_resolution_is_an_edit_that_closes_the_peer_copy(nodes, dirs):
    import api_server
    from auth import get_current_user
    from fastapi.testclient import TestClient

    local, cloud = nodes
    conflict = run(_conflicts(local, field="reference_number", status="open"))[0]
    url = f"/api/admin/sync/conflicts/{conflict['id']}/resolve"
    with local.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = _admin
        try:
            listed = http.get("/api/admin/sync/conflicts").json()
            ids = [i["id"] for i in listed["items"] if i["field"] == "reference_number"]
            assert ids == [str(conflict["id"])]
            bad = http.post(url, json={"action": "retry"})
            assert (
                bad.status_code == 400 and "ne s'applique pas" in bad.json()["detail"]
            )
            assert http.post(url, json={"action": "manual"}).status_code == 400
            r = http.post(url, json={"action": "keep_local"})
            assert r.status_code == 200 and r.json()["written"] is True, r.text
            assert http.post(url, json={"action": "dismiss"}).status_code == 409
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)

    assert run(_doc(local, DATA["doc"]))["reference_number"] == "REF-L2"
    run(_sync(local, cloud))
    assert run(_doc(cloud, DATA["doc"]))["reference_number"] == "REF-L2"
    closed = run(_conflicts(cloud, field="reference_number"))[0]
    assert (closed["status"], closed["resolution"]) == ("resolved", "superseded")
    assert (
        run(_conflicts(local, field="reference_number"))[0]["resolution"]
        == "keep_local"
    )
    resolved = run(
        local.q(
            "SELECT details FROM sdai_audit_log WHERE action = 'sync_conflict_resolved'"
        )
    )
    assert resolved and resolved[-1]["details"]["resolution"] == "keep_local"


def test_manual_resolution_types_a_new_value(nodes):
    from sync.conflicts import resolve

    local, cloud = nodes
    for node, value in ((local, "A-L"), (cloud, "A-C")):
        run(
            node.q(
                f'UPDATE "{T}" SET amount = :v WHERE id = CAST(:d AS uuid)',
                {"v": value, "d": DATA["doc"]},
            )
        )
    run(_sync(local, cloud))
    conflict = run(_conflicts(cloud, field="amount", status="open"))[0]

    async def go():
        async with cloud.engine.begin() as conn:
            return await resolve(conn, conflict, "manual", "A-AGREED", DATA["user"])

    assert run(go()) == {"resolution": "manual", "written": True}
    run(_sync(local, cloud))
    for node in nodes:
        assert run(_doc(node, DATA["doc"]))["amount"] == "A-AGREED", node.name
    # One open conflict per field: the earlier amount conflict of this
    # document was merged into this one (occurrences), now settled too.
    for node in nodes:
        rows = run(_conflicts(node, field="amount"))
        assert len(rows) == 1 and rows[0]["status"] == "resolved", node.name
        assert rows[0]["occurrences"] == 2, node.name
    assert run(_conflicts(local, field="amount"))[0]["resolution"] == "superseded"
    # Nothing left open on this document: it leaves both review queues.
    for node in nodes:
        assert _queued(node) == [], node.name


def test_another_tenant_admin_does_not_see_this_tenant_conflicts(nodes, dirs):
    import api_server
    from auth import get_current_user
    from fastapi.testclient import TestClient

    local, _ = nodes
    other = _admin()
    other.tenant_id = _id()
    with local.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = lambda: other
        try:
            items = http.get("/api/admin/sync/conflicts?status=resolved").json()[
                "items"
            ]
            assert all(i["tenant_id"] is None for i in items)
            some = run(_conflicts(local, field="amount"))[0]
            r = http.post(
                f"/api/admin/sync/conflicts/{some['id']}/resolve",
                json={"action": "dismiss"},
            )
            assert r.status_code == 404
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)


def test_new_tenant_and_document_type_appear_on_the_peer(nodes):
    import ingest_router

    local, cloud = nodes
    run(
        local.q(
            "INSERT INTO sdai_tenants (slug, name) VALUES ('land', 'Land registry')"
        )
    )
    deed = _id()

    async def create():
        with local.active():
            async with local.engine.begin() as conn:
                await ingest_router._ensure_table(
                    conn, "t_land_deed", {"parcel": "x", "owners": ["a"]}
                )
        await local.q(
            "INSERT INTO t_land_deed (id, parcel, owners) VALUES (CAST(:d AS uuid), 'P-9', '[\"Ali\"]')",
            {"d": deed},
        )
        with local.active():
            async with local.engine.begin() as conn:
                await ingest_router._ensure_table(
                    conn, "t_land_deed", {"surface": "x", "certified": True}
                )
        await local.q(
            "UPDATE t_land_deed SET surface = '12 ha', certified = true WHERE id = CAST(:d AS uuid)",
            {"d": deed},
        )

    run(create())
    summary = run(_sync(local, cloud))
    assert summary["push_errors"] == 0
    row = run(_doc(cloud, deed, "t_land_deed"))
    assert (row["parcel"], row["owners"], row["surface"], row["certified"]) == (
        "P-9",
        ["Ali"],
        "12 ha",
        True,
    )
    cols = {
        r["column_name"]: r["data_type"]
        for r in run(
            cloud.q(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 't_land_deed'"
            )
        )
    }
    assert (
        cols["owners"] == "jsonb"
        and cols["certified"] == "boolean"
        and "lifecycle_status" in cols
    )
    # The table is a full document table there: capture trigger included.
    trig = run(
        cloud.q(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'sdai_capture_change' AND tgrelid = 't_land_deed'::regclass"
        )
    )
    assert trig


def test_delete_propagates(nodes):
    local, cloud = nodes
    gid = run(
        local.one("SELECT id::text AS id FROM sdai_groups WHERE name = 'Remote team'")
    )["id"]
    run(cloud.q("DELETE FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}))
    run(_sync(local, cloud))
    assert (
        run(
            local.q("SELECT 1 FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid})
        )
        == []
    )


def test_bad_changes_are_reported_and_do_not_stall(nodes):
    from sync.store import serve_push

    local, cloud = nodes
    gid = _id()
    epoch = run(local.one("SELECT epoch::text AS e FROM sdai_sync_state"))["e"]
    cur = run(
        cloud.one("SELECT received_id FROM sdai_sync_cursors WHERE peer_node = 'local'")
    )["received_id"]
    stamp = "1700000000000:00001:local"
    changes = [
        {
            "id": cur + 1,
            "table_name": "pg_authid",
            "pk": {"id": _id()},
            "op": "insert",
            "row_data": {},
            "hlc": stamp,
            "origin_node": "local",
        },
        # Same slug as the existing tenant, different id: unique violation.
        {
            "id": cur + 2,
            "table_name": "sdai_tenants",
            "pk": {"id": _id()},
            "op": "insert",
            "row_data": {"slug": "default", "name": "Dup"},
            "hlc": stamp,
            "origin_node": "local",
        },
        {
            "id": cur + 3,
            "table_name": "sdai_groups",
            "pk": {"id": gid},
            "op": "insert",
            "row_data": {
                "id": gid,
                "tenant_id": DATA["tenant"],
                "name": "After the bad ones",
            },
            "hlc": stamp,
            "origin_node": "local",
        },
        {
            "id": cur + 4,
            "table_name": "sdai_groups",
            "pk": {"id": gid},
            "op": "update",
            "row_data": {"name": "x", 'evil"col': 1},
            "hlc": stamp,
            "origin_node": "local",
        },
    ]

    async def push():
        with cloud.active():
            async with cloud.engine.begin() as conn:
                return await serve_push(conn, "local", epoch, _wire(changes))

    response, result = run(push())
    assert (
        response["ack"] == cur + 4
        and response["applied"] == 1
        and response["errors"] == 3
    )
    assert sorted(e["kind"] for e in result.errors) == [
        "unique_violation",
        "unknown_column",
        "unknown_table",
    ]
    assert (
        run(
            cloud.one(
                "SELECT name FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}
            )
        )["name"]
        == "After the bad ones"
    )

    stored = run(_conflicts(cloud, status="open", remote_node="local"))
    assert sorted(c["kind"] for c in stored if c["kind"] != "field") == [
        "unique_violation",
        "unknown_column",
        "unknown_table",
    ]
    dup = next(c for c in stored if c["kind"] == "unique_violation")
    assert dup["change"]["row_data"]["name"] == "Dup" and "slug" in dup["message"]

    # Re-sending the same page (a retry after a lost response) changes nothing.
    response, _ = run(push())
    assert (
        response["applied"] == 0
        and response["skipped"] == 0
        and response["ack"] == cur + 4
    )
    run(
        local.q(
            "UPDATE sdai_sync_cursors SET sent_id = :s WHERE peer_node = 'cloud'",
            {"s": cur + 4},
        )
    )


def test_failed_change_can_be_retried_once_its_cause_is_fixed(nodes):
    from sync.conflicts import ResolveError, resolve
    from sync.store import serve_push

    local, cloud = nodes
    gid = _id()
    epoch = run(local.one("SELECT epoch::text AS e FROM sdai_sync_state"))["e"]
    cur = run(
        cloud.one("SELECT received_id FROM sdai_sync_cursors WHERE peer_node = 'local'")
    )["received_id"]
    membership = {
        "id": cur + 1,
        "table_name": "sdai_user_groups",
        "pk": {"user_id": DATA["user"], "group_id": gid},
        "op": "insert",
        "row_data": {"user_id": DATA["user"], "group_id": gid},
        "hlc": "1700000000000:00002:local",
        "origin_node": "local",
    }

    async def push():
        with cloud.active():
            async with cloud.engine.begin() as conn:
                return await serve_push(conn, "local", epoch, _wire([membership]))

    response, _ = run(push())
    assert response["errors"] == 1
    failed = run(_conflicts(cloud, kind="missing_parent", status="open"))[0]

    async def retry():
        async with cloud.engine.begin() as conn:
            return await resolve(conn, failed, "retry", None, DATA["user"])

    with pytest.raises(ResolveError):
        run(retry())
    run(
        cloud.q(
            "INSERT INTO sdai_groups (id, tenant_id, name)"
            " VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'Parent')",
            {"g": gid, "t": DATA["tenant"]},
        )
    )
    assert run(retry()) == {"resolution": "retry", "written": True}
    assert run(
        cloud.q(
            "SELECT 1 FROM sdai_user_groups WHERE group_id = CAST(:g AS uuid)",
            {"g": gid},
        )
    )
    run(
        local.q(
            "UPDATE sdai_sync_cursors SET sent_id = :s WHERE peer_node = 'cloud'",
            {"s": cur + 1},
        )
    )


def test_remote_stamp_ahead_raises_the_clock_floor(nodes):
    from sync.apply import apply_changes
    from sync.hlc import parse_hlc

    _, cloud = nodes
    future = "9000000000000:00001:local"
    gid = _id()

    async def go():
        async with cloud.engine.begin() as conn:
            await apply_changes(
                conn,
                [
                    {
                        "id": 1,
                        "table_name": "sdai_groups",
                        "pk": {"id": gid},
                        "op": "insert",
                        "row_data": {
                            "id": gid,
                            "tenant_id": DATA["tenant"],
                            "name": "future",
                        },
                        "hlc": future,
                        "origin_node": "local",
                    }
                ],
            )
        return (await cloud.one("SELECT sdai_hlc_now() AS h"))["h"]

    try:
        assert parse_hlc(run(go())).ms > parse_hlc(future).ms
    finally:
        run(cloud.q("DELETE FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}))
        run(cloud.q("UPDATE sdai_sync_state SET hlc_floor_ms = 0"))


def test_reseeded_changelog_restarts_cleanly(nodes):
    from sync.capture import seed_changelog

    local, cloud = nodes

    async def reseed():
        async with local.engine.begin() as conn:
            await seed_changelog(conn, force=True)

    before = run(_doc(local, DATA["doc"]))
    run(reseed())
    summary = run(_sync(local, cloud))
    # The peer noticed the new epoch and took the whole snapshot again, as
    # merges onto rows it already has - no errors, no conflicts.
    assert (
        summary["pushed"] > 0
        and summary["push_errors"] == 0
        and summary["push_conflicts"] == 0
    ), summary
    for node in nodes:
        doc = run(_doc(node, DATA["doc"]))
        assert (doc["reference_number"], doc["amount"]) == (
            before["reference_number"],
            before["amount"],
        ), node.name


def test_reseed_never_overwrites_a_newer_unsynced_edit(nodes):
    from sync.capture import seed_changelog

    local, cloud = nodes
    # Edited on the cloud, not synced yet - then the local node reseeds,
    # sending its older value of that field as a fresh insert.
    run(
        cloud.q(
            f"UPDATE \"{T}\" SET amount = '400-cloud' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )

    async def reseed():
        async with local.engine.begin() as conn:
            await seed_changelog(conn, force=True)

    run(reseed())
    summary = run(_sync(local, cloud))
    assert summary["push_errors"] == 0 and summary["errors"] == 0
    for node in nodes:
        assert run(_doc(node, DATA["doc"]))["amount"] == "400-cloud", node.name


def test_push_and_pull_over_signed_http(nodes, dirs, monkeypatch):
    """The real handlers on the local node, called by the cloud's worker
    code over HTTP (TestClient) - signature, gzip and JSON included."""
    import api_server
    from fastapi.testclient import TestClient
    from sync import config as sync_config
    from sync.worker import HttpPeer, sync_once

    local, cloud = nodes
    monkeypatch.setattr(sync_config, "SYNC_NODE_TOKEN", "two-node-token")
    gid = _id()
    run(
        cloud.q(
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'Via HTTP')",
            {"g": gid, "t": DATA["tenant"]},
        )
    )
    run(
        local.q(
            f"UPDATE \"{T}\" SET reference_number = 'REF-HTTP' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )

    with local.active(), TestClient(api_server.app) as http:
        peer = HttpPeer("http://testserver", "cloud", "two-node-token")

        class Transport:
            """httpx -> the in-process app, keeping HttpPeer's signing."""

            async def request(self, method, url, content=b"", headers=None):
                path = url.removeprefix("http://testserver")
                return http.request(method, path, content=content, headers=headers)

        peer._http = Transport()
        # The cloud node is the initiator here: its engine, its changelog.
        summary = asyncio.run(sync_once(cloud.engine, peer))
    assert (
        summary["peer"] == "local" and summary["pushed"] >= 1 and summary["pulled"] >= 1
    ), summary
    assert (
        run(
            local.one(
                "SELECT name FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}
            )
        )["name"]
        == "Via HTTP"
    )
    assert run(_doc(cloud, DATA["doc"]))["reference_number"] == "REF-HTTP"


def test_pruned_history_triggers_a_snapshot_for_a_peer_starting_over(nodes):
    from sync.store import prune_changelog

    local, cloud = nodes
    run(
        local.q(
            f"UPDATE \"{T}\" SET reference_number = 'REF-PRUNE' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    run(_sync(local, cloud))
    run(
        local.q(
            "UPDATE sdai_sync_changelog SET created_at = now() - interval '40 days'"
        )
    )

    async def prune():
        async with local.engine.begin() as conn:
            return await prune_changelog(conn, 30)

    assert run(prune()) > 0
    assert run(local.one("SELECT count(*) AS n FROM sdai_sync_changelog"))["n"] == 0
    # The cloud forgets how far it got (e.g. restored from an old backup).
    run(
        cloud.q(
            "UPDATE sdai_sync_cursors SET received_id = 0 WHERE peer_node = 'local'"
        )
    )
    epoch = run(local.one("SELECT epoch::text AS e FROM sdai_sync_state"))["e"]
    summary = run(_sync(local, cloud))
    assert run(local.one("SELECT epoch::text AS e FROM sdai_sync_state"))["e"] != epoch
    assert (
        summary["pushed"] > 0
        and summary["push_errors"] == 0
        and summary["push_conflicts"] == 0
    )
    assert run(_doc(cloud, DATA["doc"]))["reference_number"] == "REF-PRUNE"


def test_nothing_is_pruned_before_any_peer_synced(nodes):
    from sqlalchemy import text
    from sync.store import prune_changelog

    local, _ = nodes

    async def go():
        async with local.engine.connect() as conn:
            async with conn.begin() as tx:
                await conn.execute(text("DELETE FROM sdai_sync_cursors"))
                deleted = await prune_changelog(conn, 0)
                await tx.rollback()
                return deleted

    assert run(go()) == 0


# ── 8.5 files ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def data_root(monkeypatch):
    from pathlib import Path

    from sync import config as sync_config

    monkeypatch.setattr(sync_config, "file_roots", lambda: [Path("/data")])
    monkeypatch.setattr(sync_config, "FILE_CHUNK_BYTES", 64 * 1024)


def _put(node, logical, content: bytes):
    real = node.disk / logical.lstrip("/")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(content)
    return real


def _on_disk(node, logical):
    real = node.disk / logical.lstrip("/")
    return real.read_bytes() if real.is_file() else None


async def _queue(node):
    return {
        (r["path"], r["direction"]): r["status"]
        for r in await node.q(
            "SELECT path, direction, status FROM sdai_sync_file_queue"
        )
    }


def test_local_files_are_pushed_and_cloud_files_fetched(nodes, data_root):
    local, cloud = nodes
    original = bytes(range(256)) * 1000  # several chunks
    page = b"\x89PNG\r\n\x1a\n" + b"p" * 5000
    _put(local, "/data/uploads/b1/deed.pdf", original)
    _put(local, "/data/images/b1_p1.png", page)
    run(
        local.q(
            f"UPDATE \"{T}\" SET original_path = '/data/uploads/b1/deed.pdf',"
            " page_image_paths = '[\"/data/images/b1_p1.png\"]' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    version = b"%PDF-1.4 cloud version"
    vpath = f"/data/uploads/versions/default/{DATA['doc']}/v9_scan.pdf"
    _put(cloud, vpath, version)
    run(
        cloud.q(
            "INSERT INTO sdai_document_versions (tenant_id, table_name, document_id, version_no, file_path)"
            " VALUES (CAST(:t AS uuid), :tb, CAST(:d AS uuid), 9, :p)",
            {"t": DATA["tenant"], "tb": T, "d": DATA["doc"], "p": vpath},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["files_done"] == 3 and summary["files_failed"] == 0, summary
    assert _on_disk(cloud, "/data/uploads/b1/deed.pdf") == original
    assert _on_disk(cloud, "/data/images/b1_p1.png") == page
    assert _on_disk(local, vpath) == version
    queue = run(_queue(local))
    assert queue[("/data/uploads/b1/deed.pdf", "push")] == "done"
    assert queue[(vpath, "fetch")] == "done"
    # No temp files left behind on either disk.
    for node in nodes:
        assert not list(node.disk.rglob("*.sync-part")), node.name


def test_missing_file_waits_shows_pending_and_arrives_later(
    nodes, data_root, monkeypatch
):
    from sync import config as sync_config
    from sync import files

    local, cloud = nodes
    later = "/data/images/later.png"
    run(
        local.q(
            f'UPDATE "{T}" SET source_image_path = :p WHERE id = CAST(:d AS uuid)',
            {"p": later, "d": DATA["doc"]},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["files_waiting"] == 1
    assert run(_queue(local))[(later, "push")] == "waiting"

    monkeypatch.setattr(sync_config, "SYNC_NODE_TOKEN", "t")

    async def pending(node):
        with node.active():
            async with node.engine.connect() as conn:
                return await files.file_pending(conn, later)

    assert run(pending(local)) is True  # still queued here
    assert run(pending(cloud)) is True  # cloud: referenced, not there yet

    _put(local, later, b"\x89PNG late")
    run(
        local.q(
            "UPDATE sdai_sync_file_queue SET next_attempt_at = now() WHERE path = :p",
            {"p": later},
        )
    )
    run(_sync(local, cloud))
    assert _on_disk(cloud, later) == b"\x89PNG late"
    assert run(pending(local)) is False


def test_bad_paths_and_different_files_fail_without_overwriting(nodes, data_root):
    local, cloud = nodes
    clash = "/data/images/clash.png"
    _put(local, clash, b"local content")
    _put(cloud, clash, b"cloud content")
    run(
        local.q(
            f"UPDATE \"{T}\" SET source_pdf_path = '/etc/passwd', source_image_path = :p"
            " WHERE id = CAST(:d AS uuid)",
            {"p": clash, "d": DATA["doc"]},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["files_failed"] == 2, summary
    queue = run(_queue(local))
    assert queue[("/etc/passwd", "push")] == "failed"
    assert queue[(clash, "push")] == "failed"
    assert _on_disk(cloud, clash) == b"cloud content"
    assert _on_disk(local, clash) == b"local content"


def test_interrupted_fetch_resumes_from_the_part_file(nodes, data_root):
    from sync import files

    local, cloud = nodes
    big = bytes(range(200)) * 2000
    path = "/data/uploads/b2/big.pdf"
    _put(cloud, path, big)
    # A previous cycle got the first two chunks before the link dropped.
    part = files.part_of(local.disk / path.lstrip("/"))
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(big[: 2 * 64 * 1024])
    run(
        cloud.q(
            "INSERT INTO sdai_document_versions (tenant_id, table_name, document_id, version_no, file_path)"
            " VALUES (CAST(:t AS uuid), :tb, CAST(:d AS uuid), 10, :p)",
            {"t": DATA["tenant"], "tb": T, "d": DATA["doc"], "p": path},
        )
    )
    summary = run(_sync(local, cloud))
    assert summary["file_bytes"] == len(big) - 2 * 64 * 1024, summary
    assert _on_disk(local, path) == big


# ── 8.6 cloud node mode ───────────────────────────────────────────────────────


def test_cloud_upload_waits_then_is_processed_locally_and_syncs_back(
    nodes, data_root, dirs, monkeypatch, tmp_path
):
    import api_server
    import ingest_router
    from auth import get_current_user
    from fastapi.testclient import TestClient

    local, cloud = nodes
    png = b"\x89PNG\r\n\x1a\n" + b"scan" * 100

    # 1. Upload through the real endpoint on the cloud node: stored, left
    #    pending, never handed to a (non-existent) pipeline there.
    while not ingest_router._queue.empty():
        ingest_router._queue.get_nowait()
    upload_dir = tmp_path / "cloud-uploads"
    monkeypatch.setattr(ingest_router, "UPLOADS_DIR", upload_dir)
    with cloud.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = _admin
        try:
            r = http.post(
                "/api/ingest/upload", files={"files": ("scan.png", png, "image/png")}
            )
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert ingest_router._queue.empty()
    bd = run(
        cloud.one(
            "SELECT * FROM batch_documents WHERE batch_id = CAST(:b AS uuid)",
            {"b": batch_id},
        )
    )
    assert bd["status"] == "pending" and bd["origin_node"] == "cloud"

    # Same absolute path on both nodes: put the file where the sync expects
    # it on the cloud's disk and point the row at that logical path.
    logical = f"/data/uploads/{batch_id}/scan.png"
    _put(cloud, logical, png)
    run(
        cloud.q(
            "UPDATE batch_documents SET source_path = :p WHERE id = :i",
            {"p": logical, "i": bd["id"]},
        )
    )

    # 2. Before its file arrived, the local node does not pick it up.
    run(_sync_rows_only(local, cloud))
    with local.active():
        assert run(ingest_router._pick_up_peer_uploads()) == 0
    # 3. Next cycle brings the file; the local node queues it once.
    run(_sync(local, cloud))
    assert _on_disk(local, logical) == png
    with local.active():
        assert run(ingest_router._pick_up_peer_uploads()) == 1
        assert run(ingest_router._pick_up_peer_uploads()) == 0
    item = ingest_router._queue.get_nowait()
    assert (
        item[0] == str(bd["id"]) and item[1] == batch_id and item[2].read_bytes() == png
    )

    # 4. The local pipeline files it (Stage 3 stand-in) - record_id is
    #    assigned here - and the result syncs back to the cloud.
    async def file_it():
        with local.active():
            await ingest_router._update_doc_status(str(bd["id"]), "processing")
            await ingest_router._store_extraction(
                batch_id,
                logical,
                {"document_type": "invoice", "reference_number": "CLOUD-1"},
                "high",
                "approved",
                DATA["tenant"],
                "default",
                content_hash=item[4],
                batch_document_id=str(bd["id"]),
            )
            await ingest_router._update_doc_status(
                str(bd["id"]), "completed", document_type="invoice"
            )

    run(file_it())
    run(_sync(local, cloud))
    filed = run(
        cloud.one(
            f"SELECT record_id, origin_node FROM \"{T}\" WHERE reference_number = 'CLOUD-1'"
        )
    )
    assert filed["record_id"].startswith("DEFAULT-") and filed["origin_node"] == "local"
    status = run(
        cloud.one("SELECT status FROM batch_documents WHERE id = :i", {"i": bd["id"]})
    )
    assert status["status"] == "completed"


async def _sync_rows_only(local, cloud):
    """A cycle that moves rows but no files yet (link too slow, say)."""
    from sync import worker

    async def no_files(engine, client):
        return {}

    saved = worker.transfer_files
    worker.transfer_files = no_files
    try:
        return await _sync(local, cloud)
    finally:
        worker.transfer_files = saved


def test_cloud_never_numbers_or_extracts(nodes, dirs):
    import api_server
    import ingest_router
    from auth import get_current_user
    from fastapi.testclient import TestClient

    _, cloud = nodes

    async def number():
        with cloud.active():
            async with cloud.engine.begin() as conn:
                await ingest_router._next_record_id(conn, DATA["tenant"], "default")

    with pytest.raises(RuntimeError):
        run(number())
    with cloud.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = _admin
        try:
            r = http.post(f"/api/ingest/pages/{_id()}/retry")
            assert r.status_code == 409 and "serveur local" in r.json()["detail"]
            r = http.post(f"/api/review/{T}/{DATA['doc']}/retry")
            assert r.status_code == 409
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)


# ── Restores: a node goes back in time or starts empty ────────────────────────


async def _admin_sql(*statements):
    """Run statements on the server's maintenance database (copying or
    replacing the test databases needs no session on them)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    base = TEST_DATABASE_URL.rpartition("/")[0]
    admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    await admin.dispose()


def _db_name(url):
    return url.rpartition("/")[2].split("?")[0]


async def _file_doc(node, ref):
    import ingest_router

    with node.active():
        batch = await ingest_router._create_batch("upload", DATA["tenant"])
        await ingest_router._store_extraction(
            batch,
            f"/data/images/{ref}.png",
            {"document_type": "invoice", "reference_number": ref},
            "high",
            "approved",
            DATA["tenant"],
            "default",
            content_hash=f"hash-{ref}",
        )
    return (
        await node.one(
            f'SELECT record_id FROM "{T}" WHERE reference_number = :r', {"r": ref}
        )
    )["record_id"]


async def _snapshot(node):
    return {
        "docs": sorted(
            (str(r["id"]), r["reference_number"], r["record_id"], r["amount"])
            for r in await node.q(
                f'SELECT id, reference_number, record_id, amount FROM "{T}"'
            )
        ),
        "groups": sorted(
            (str(r["id"]), r["name"])
            for r in await node.q("SELECT id, name FROM sdai_groups")
        ),
        "users": sorted(
            str(r["id"]) for r in await node.q("SELECT id FROM sdai_users")
        ),
    }


def test_local_restored_from_backup_gets_its_later_work_back(nodes, data_root):
    local, cloud = nodes
    db = _db_name(TEST_DATABASE_URL)
    run(_sync(local, cloud))
    run(
        _admin_sql(
            "DROP DATABASE IF EXISTS sdai_backup_copy",
            f"CREATE DATABASE sdai_backup_copy TEMPLATE {db}",
        )
    )

    # After the backup: the local node files a document (record_id N), the
    # cloud adds a group, both reach the other side.
    lost_record = run(_file_doc(local, "AFTER-BACKUP"))
    gid = _id()
    run(
        cloud.q(
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'After backup')",
            {"g": gid, "t": DATA["tenant"]},
        )
    )
    run(_sync(local, cloud))
    assert run(
        cloud.one(f'SELECT 1 AS x FROM "{T}" WHERE record_id = :r', {"r": lost_record})
    )

    # Disaster: the local server is restored from last night's backup.
    run(
        _admin_sql(
            f"DROP DATABASE {db}",
            f"CREATE DATABASE {db} TEMPLATE sdai_backup_copy",
            "DROP DATABASE sdai_backup_copy",
        )
    )
    assert (
        run(local.q(f'SELECT 1 FROM "{T}" WHERE record_id = :r', {"r": lost_record}))
        == []
    )

    run(_sync(local, cloud))
    run(_sync(local, cloud))
    # Its own later document came back from the cloud, and the cloud's group.
    back = run(
        local.one(
            f'SELECT reference_number FROM "{T}" WHERE record_id = :r',
            {"r": lost_record},
        )
    )
    assert back == {"reference_number": "AFTER-BACKUP"}
    assert run(
        local.one(
            "SELECT name FROM sdai_groups WHERE id = CAST(:g AS uuid)", {"g": gid}
        )
    )
    assert run(_snapshot(local)) == run(_snapshot(cloud))
    # Numbering continues after the recovered document - no duplicate.
    nxt = run(_file_doc(local, "AFTER-RESTORE"))
    assert int(nxt.rsplit("-", 1)[1]) == int(lost_record.rsplit("-", 1)[1]) + 1
    run(_sync(local, cloud))
    assert run(_snapshot(local)) == run(_snapshot(cloud))


def test_cloud_rebuilt_from_scratch_gets_everything_back(nodes, data_root):
    import ingest_router
    from sync.join import prepare_fresh_node

    local, cloud = nodes
    gid = _id()
    run(
        cloud.q(
            "INSERT INTO sdai_groups (id, tenant_id, name) VALUES (CAST(:g AS uuid), CAST(:t AS uuid), 'Cloud only')",
            {"g": gid, "t": DATA["tenant"]},
        )
    )
    run(_sync(local, cloud))
    before = run(_snapshot(local))
    assert (gid, "Cloud only") in before["groups"]

    # The cloud VM is lost: a new one, empty, joins.
    async def rebuild():
        await _reset(cloud)
        async with cloud.engine.begin() as conn:
            await prepare_fresh_node(conn)
        with cloud.active():
            await ingest_router._init_db()

    run(rebuild())
    run(_sync(local, cloud))
    run(_sync(local, cloud))
    assert run(_snapshot(cloud)) == before
    # And it keeps working both ways afterwards.
    run(
        cloud.q(
            f"UPDATE \"{T}\" SET amount = 'post-rebuild' WHERE id = CAST(:d AS uuid)",
            {"d": DATA["doc"]},
        )
    )
    run(_sync(local, cloud))
    assert run(_doc(local, DATA["doc"]))["amount"] == "post-rebuild"


# ── 8.8 admin status ──────────────────────────────────────────────────────────


def _admin_get(node, path, monkeypatch, **config):
    import api_server
    from auth import get_current_user
    from fastapi.testclient import TestClient
    from sync import config as sync_config

    for key, value in config.items():
        monkeypatch.setattr(sync_config, key, value)
    with node.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = _admin
        try:
            return http.get(path).json()
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)


def test_status_reports_peers_files_backups_and_alerts(nodes, dirs, monkeypatch):
    local, cloud = nodes
    run(_sync(local, cloud))
    on = {"SYNC_NODE_TOKEN": "t", "SYNC_PEER_URL": "https://cloud.example"}

    run(local.q("DELETE FROM sdai_backup_runs"))
    status = _admin_get(local, "/api/admin/sync/status", monkeypatch, **on)
    assert status["node"] == {
        "node_id": "local",
        "node_role": "local",
        "sync_enabled": True,
        "initiator": True,
        "peer_url": "https://cloud.example",
        "join_pending": False,
    }
    peer = status["peers"][0]
    assert peer["peer_node"] == "cloud" and peer["pending_changes"] == 0
    kinds = {a["kind"] for a in status["alerts"]}
    assert "backup_not_configured" in kinds

    # A failed nightly backup and a sync that has been down for 10 hours.
    run(
        local.q(
            "INSERT INTO sdai_backup_runs (node_id, started_at, finished_at, status, target)"
            " VALUES ('local', now() - interval '2 days', now() - interval '2 days', 'success', 'sftp'),"
            "        ('local', now() - interval '3 hours', now() - interval '3 hours', 'failed', 'nas')"
        )
    )
    run(
        local.q(
            "UPDATE sdai_sync_state SET last_success_at = now() - interval '10 hours'"
        )
    )
    status = _admin_get(local, "/api/admin/sync/status", monkeypatch, **on)
    alerts = {a["kind"]: a for a in status["alerts"]}
    assert alerts["sync_down"]["hours"] == 6
    assert "backup_failed" in alerts and alerts["backup_failed"]["since"]
    assert {b["target"]: b["status"] for b in status["backups"]} == {
        "sftp": "success",
        "nas": "failed",
    }
    assert status["files"]["failed"] >= 1  # the /etc/passwd + clash files of 8.5
    assert alerts["files_failed"]["count"] == status["files"]["failed"]

    # The passive cloud node judges sync by when the local node last reached it.
    cloud_status = _admin_get(
        cloud,
        "/api/admin/sync/status",
        monkeypatch,
        SYNC_NODE_TOKEN="t",
        SYNC_PEER_URL="",
    )
    assert cloud_status["node"]["initiator"] is False
    assert "sync_down" not in {a["kind"] for a in cloud_status["alerts"]}
    run(
        cloud.q(
            "UPDATE sdai_sync_cursors SET last_contact_at = now() - interval '1 day'"
        )
    )
    cloud_status = _admin_get(
        cloud,
        "/api/admin/sync/status",
        monkeypatch,
        SYNC_NODE_TOKEN="t",
        SYNC_PEER_URL="",
    )
    assert "sync_down" in {a["kind"] for a in cloud_status["alerts"]}


def test_problem_files_listed_and_retried(nodes, dirs, monkeypatch):
    import api_server
    from auth import get_current_user
    from fastapi.testclient import TestClient

    local, _ = nodes
    listed = _admin_get(local, "/api/admin/sync/files", monkeypatch)
    assert "/etc/passwd" in {f["path"] for f in listed["items"]}
    with local.active(), TestClient(api_server.app) as http:
        api_server.app.dependency_overrides[get_current_user] = _admin
        try:
            assert http.post("/api/admin/sync/files/retry").json()["retried"] >= 1
        finally:
            api_server.app.dependency_overrides.pop(get_current_user, None)
    assert (
        run(local.q("SELECT 1 FROM sdai_sync_file_queue WHERE status = 'failed'")) == []
    )
