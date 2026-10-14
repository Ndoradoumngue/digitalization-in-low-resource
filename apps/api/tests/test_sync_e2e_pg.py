"""End to end, real processes (GED Phase 8.10): a cloud API, a local API and
the local sync worker (`python -m sync.worker`) talking over HTTP, two
PostgreSQL databases. Users edit both sides through the API while the
worker is stopped ("disconnected"), then the worker comes back and both
nodes must converge - conflicts listed, nothing lost. Opt-in like every
real-PostgreSQL test:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_sync_e2e_pg.py -m pg

Uses (and wipes) the same two databases as test_sync_twonode_pg.py.
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"),
]

API_DIR = Path(__file__).resolve().parents[1]
TOKEN = "e2e-sync-token"
SECRET = "e2e-shared-auth-secret"
T = "t_default_invoice"


def _peer_url() -> str:
    base, _, db = TEST_DATABASE_URL.rpartition("/")
    return os.getenv("TEST_PEER_DATABASE_URL") or f"{base}/{db.split('?')[0]}_peer"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(node_id, role, db_url, data_dir, **extra):
    env = {
        **os.environ,
        "AUTH_SECRET_KEY": SECRET,
        "DATABASE_URL": db_url,
        "NODE_ID": node_id,
        "NODE_ROLE": role,
        "SYNC_NODE_TOKEN": TOKEN,
        "DATA_DIR": str(data_dir),
        "OLLAMA_HOST": "http://127.0.0.1:9",
        "REDIS_URL": "",
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("SYNC_PEER_URL", None)
    env.update(extra)
    return env


class Proc:
    def __init__(self, name, args, env, log_dir):
        self.name = name
        self.log = open(log_dir / f"{name}.log", "a")
        self.p = subprocess.Popen(
            [sys.executable, *args],
            cwd=API_DIR,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGTERM)
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.log.close()


def _wait_http(port, proc, timeout=60):
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.p.poll() is not None:
            raise RuntimeError(f"{proc.name} exited - see its log")
        try:
            if (
                httpx.get(
                    f"http://127.0.0.1:{port}/openapi.json", timeout=2
                ).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"{proc.name} did not start")


async def _sql(url, sql, params=None, node=None):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # Never wait forever on another session's lock - fail with a
            # diagnosis instead (see _wait_for).
            await conn.execute(text("SET LOCAL lock_timeout = '20s'"))
            if node:
                await conn.execute(text(f"SET LOCAL sdai.node_id = '{node}'"))
            result = await conn.execute(text(sql), params or {})
            return (
                [dict(r) for r in result.mappings().all()]
                if result.returns_rows
                else []
            )
    finally:
        await engine.dispose()


def sql(url, statement, params=None, node=None):
    return asyncio.run(_sql(url, statement, params, node))


async def _ensure_table(url, node, table, fields):
    import ingest_router
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL sdai.node_id = '{node}'"))
            await ingest_router._ensure_table(conn, table, fields)
    finally:
        await engine.dispose()


def _db_activity() -> str:
    """What every session on the test server is doing - attached to a
    timeout so a stuck run explains itself."""
    base = TEST_DATABASE_URL.rpartition("/")[0]
    try:
        rows = sql(
            f"{base}/postgres",
            "SELECT datname, application_name, state, wait_event_type, wait_event,"
            " left(query, 160) AS query, now() - xact_start AS xact_age FROM pg_stat_activity"
            " WHERE datname LIKE 'sdai_test%' AND pid <> pg_backend_pid()",
        )
        return "\n".join(str(r) for r in rows)
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return f"(could not read pg_stat_activity: {exc})"


def _wait_for(check, timeout=90, what="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = check()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 - keep polling
            last = exc
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for {what}: {last!r}\nDB sessions:\n{_db_activity()}")


def _snapshot(url):
    docs = sql(
        url,
        f'SELECT id::text, reference_number, amount, note, record_id FROM "{T}" ORDER BY id',
    )
    contracts = sql(
        url,
        "SELECT id::text, party FROM t_default_contract ORDER BY id"
        if sql(url, "SELECT to_regclass('t_default_contract') IS NOT NULL AS x")[0]["x"]
        else "SELECT NULL::text AS id, NULL::text AS party WHERE false",
    )
    groups = sql(url, "SELECT id::text, name FROM sdai_groups ORDER BY id")
    return {"docs": docs, "contracts": contracts, "groups": groups}


@pytest.fixture(scope="module")
def deployment(tmp_path_factory):
    import bcrypt

    logs = tmp_path_factory.mktemp("e2e-logs")
    local_url, cloud_url = TEST_DATABASE_URL, _peer_url()
    base = TEST_DATABASE_URL.rpartition("/")[0]
    peer_db = cloud_url.rpartition("/")[2].split("?")[0]
    exists = sql(
        f"{base}/postgres",
        "SELECT 1 AS x FROM pg_database WHERE datname = :d",
        {"d": peer_db},
    )
    if not exists:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        async def create():
            admin = create_async_engine(
                f"{base}/postgres", isolation_level="AUTOCOMMIT"
            )
            async with admin.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{peer_db}"'))
            await admin.dispose()

        asyncio.run(create())
    for url in (local_url, cloud_url):
        sql(url, "DROP SCHEMA public CASCADE")
        sql(url, "CREATE SCHEMA public")

    local_port, cloud_port = _free_port(), _free_port()
    local_env = _env(
        "local-e2e",
        "local",
        local_url,
        tmp_path_factory.mktemp("local-data"),
        SYNC_PEER_URL=f"http://127.0.0.1:{cloud_port}",
        SYNC_INTERVAL_SECONDS="1",
    )
    cloud_env = _env(
        "cloud-e2e", "cloud", cloud_url, tmp_path_factory.mktemp("cloud-data")
    )
    procs = {}
    try:
        # The fresh cloud node joins before it ever starts.
        join = subprocess.run(
            [sys.executable, "-m", "sync.join"],
            cwd=API_DIR,
            env=cloud_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert join.returncode == 0, join.stdout + join.stderr
        procs["cloud"] = Proc(
            "cloud-api",
            ["-m", "uvicorn", "api_server:app", "--port", str(cloud_port)],
            cloud_env,
            logs,
        )
        procs["local"] = Proc(
            "local-api",
            ["-m", "uvicorn", "api_server:app", "--port", str(local_port)],
            local_env,
            logs,
        )
        _wait_http(cloud_port, procs["cloud"])
        _wait_http(local_port, procs["local"])

        # Data that exists on the local node before sync starts.
        tenant = sql(
            local_url, "SELECT id::text FROM sdai_tenants WHERE slug = 'default'"
        )[0]["id"]
        hashed = bcrypt.hashpw(b"secret-pass", bcrypt.gensalt()).decode()
        sql(
            local_url,
            "INSERT INTO sdai_users (email, hashed_password, role, tenant_id)"
            " VALUES ('admin@e2e.example.org', :h, 'admin', CAST(:t AS uuid))",
            {"h": hashed, "t": tenant},
            node="local-e2e",
        )
        asyncio.run(
            _ensure_table(
                local_url,
                "local-e2e",
                T,
                {"reference_number": "x", "amount": "x", "note": "x"},
            )
        )
        docs = [str(uuid.uuid4()), str(uuid.uuid4())]
        for i, doc in enumerate(docs):
            sql(
                local_url,
                f'INSERT INTO "{T}" (id, reference_number, amount, note, record_id, review_status)'
                " VALUES (CAST(:d AS uuid), :r, '10', 'n0', :rid, 'approved')",
                {"d": doc, "r": f"REF-{i}", "rid": f"DEFAULT-1999-00000{i}"},
                node="local-e2e",
            )

        def start_worker():
            procs["worker"] = Proc(
                "local-sync-worker", ["-m", "sync.worker"], local_env, logs
            )

        start_worker()
        yield {
            "local_url": local_url,
            "cloud_url": cloud_url,
            "procs": procs,
            "start_worker": start_worker,
            "local_api": f"http://127.0.0.1:{local_port}",
            "cloud_api": f"http://127.0.0.1:{cloud_port}",
            "docs": docs,
            "logs": logs,
        }
    finally:
        for proc in procs.values():
            proc.stop()
        # The APIs made their NODE_ID the databases' default (_register_node).
        for url in (local_url, cloud_url):
            name = url.rpartition("/")[2].split("?")[0]
            sql(f"{base}/postgres", f'ALTER DATABASE "{name}" RESET sdai.node_id')


def _login(base_url):
    import httpx

    client = httpx.Client(base_url=base_url, timeout=30)
    r = client.post(
        "/api/auth/login", json={"email": "admin@e2e.example.org", "password": "secret-pass"}
    )
    assert r.status_code == 200, r.text
    return client


def _edit(client, doc, **fields):
    r = client.patch(f"/api/db/documents/{T}/{doc}/fields", json={"fields": fields})
    assert r.status_code == 200, r.text


def test_disconnected_edits_on_both_sides_converge(deployment):
    d = deployment
    local_url, cloud_url = d["local_url"], d["cloud_url"]
    doc1, doc2 = d["docs"]

    # 1. First sync: the cloud receives the deployment; the same account
    #    logs in on both nodes (shared users, same AUTH_SECRET_KEY).
    _wait_for(
        lambda: len(sql(cloud_url, f'SELECT 1 FROM "{T}"')) == 2, what="first sync"
    )
    local, cloud = _login(d["local_api"]), _login(d["cloud_api"])

    # 2. Disconnect: stop the worker, then work on both sides.
    d["procs"]["worker"].stop()
    _edit(local, doc1, reference_number="REF-0-LOCAL")
    _edit(cloud, doc1, amount="99-CLOUD")
    _edit(local, doc2, note="note-local")
    time.sleep(0.05)
    _edit(cloud, doc2, note="note-cloud")  # later: wins everywhere
    asyncio.run(
        _ensure_table(local_url, "local-e2e", "t_default_contract", {"party": "x"})
    )
    contract = str(uuid.uuid4())
    sql(
        local_url,
        "INSERT INTO t_default_contract (id, party) VALUES (CAST(:c AS uuid), 'Acme')",
        {"c": contract},
        node="local-e2e",
    )
    r = cloud.post("/api/admin/groups", json={"name": "Remote team"})
    assert r.status_code in (200, 201), r.text
    assert _snapshot(local_url) != _snapshot(cloud_url)

    # 3. Reconnect and wait for convergence.
    d["start_worker"]()

    def converged():
        a, b = _snapshot(local_url), _snapshot(cloud_url)
        return a if a == b and a["contracts"] and len(a["groups"]) == 1 else None

    state = _wait_for(converged, what="convergence")
    by_id = {row["id"]: row for row in state["docs"]}
    assert (by_id[doc1]["reference_number"], by_id[doc1]["amount"]) == (
        "REF-0-LOCAL",
        "99-CLOUD",
    )
    assert by_id[doc2]["note"] == "note-cloud"
    assert state["contracts"] == [{"id": contract, "party": "Acme"}]
    assert state["groups"][0]["name"] == "Remote team"

    # 4. The same-field edit is listed on both nodes - nothing lost silently.
    for url in (local_url, cloud_url):
        rows = _wait_for(
            lambda u=url: sql(
                u,
                "SELECT field, local_value, remote_value FROM sdai_sync_conflicts WHERE status = 'open'",
            ),
            what="conflict rows",
        )
        assert [r["field"] for r in rows] == ["note"]
        assert {rows[0]["local_value"], rows[0]["remote_value"]} == {
            "note-local",
            "note-cloud",
        }
    conflicts = cloud.get("/api/admin/sync/conflicts").json()
    assert [c["field"] for c in conflicts["items"]] == ["note"]
    status = local.get("/api/admin/sync/status").json()
    assert (
        status["node"]["initiator"] and status["peers"][0]["peer_node"] == "cloud-e2e"
    )
    assert "conflicts_open" in {a["kind"] for a in status["alerts"]}

    # 5. Both audit chains verify on both nodes (each node's own + the peer's copy).
    for client in (local, cloud):
        verdict = client.get("/api/admin/integrity/audit-chain").json()
        assert verdict["ok"], verdict
