"""Collaboration against a real PostgreSQL (GED Phase 7): notifications and
the e-mail outbox, check-out / check-in, comments, electronic signatures.
Opt-in, like test_lifecycle_pg.py:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/empty_db \
        pytest apps/api/tests/test_collab_pg.py -m pg

The database is wiped (public schema dropped) - never point this at real data.
"""

import asyncio
import hashlib
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


def _id() -> str:
    return str(uuid.uuid4())


U = {
    k: _id()
    for k in (
        "admin",
        "admin2",
        "archivist",
        "editor",
        "rev",
        "rev2",
        "reader",
        "outsider",
    )
}
TENANT: dict = {}
ENV: dict = {}

# key -> (role, can_manage_lifecycle, can_edit_extraction)
_ROLES = {
    "admin": ("admin", False, False),
    "admin2": ("admin", False, False),
    "archivist": ("reviewer", True, True),
    "editor": ("reviewer", False, True),
    "rev": ("reviewer", False, False),
    "rev2": ("reviewer", False, False),
    "reader": ("reader", False, False),
    "outsider": ("reviewer", False, False),
}


def _user(key) -> CurrentUser:
    role, lifecycle_flag, edit_flag = _ROLES[key]
    return CurrentUser(
        id=U[key],
        email=f"{key}@example.com",
        full_name=key.capitalize(),
        role=role,
        tenant_id=TENANT["id"],
        tenant_slug="default",
        tenant_name="Default",
        can_manage_access=role == "admin",
        can_edit_extraction=edit_flag,
        group_ids=[],
        locale="en",
        can_manage_lifecycle=lifecycle_flag,
        clearance="secret",
    )


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
        for key, (role, lifecycle_flag, edit_flag) in _ROLES.items():
            await conn.execute(
                text(
                    "INSERT INTO sdai_users (id, email, full_name, hashed_password, role, tenant_id,"
                    " can_manage_lifecycle, can_edit_extraction, clearance, locale)"
                    " VALUES (CAST(:id AS uuid), :e, :n, :pw, :r, CAST(:t AS uuid), :l, :x,"
                    " 'secret', :loc)"
                ),
                {
                    "id": U[key],
                    "e": f"{key}@example.com",
                    "n": key.capitalize(),
                    "pw": ENV["pw_hash"],
                    "r": role,
                    "t": TENANT["id"],
                    "l": lifecycle_flag,
                    "x": edit_flag,
                    "loc": "fr" if key == "rev" else None,
                },
            )
        await ingest_router._ensure_table(
            conn,
            T,
            {"document_type": "x", "reference_number": "x", "amount": "x"},
        )


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    import bcrypt
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
        ingest_router.VERSIONS_DIR,
        sync_config.file_roots,
    )
    ingest_router.engine = engine
    ingest_router.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    documents_router._SAFE_FILE_ROOTS = [files_dir]
    ingest_router.VERSIONS_DIR = files_dir / "versions"
    sync_config.file_roots = lambda: [files_dir]
    ENV["dir"] = files_dir
    ENV["pw_hash"] = bcrypt.hashpw(b"secret-pw", bcrypt.gensalt(4)).decode()
    asyncio.run(_seed(engine))
    yield engine
    (
        ingest_router.engine,
        ingest_router.SessionLocal,
        documents_router._SAFE_FILE_ROOTS,
        ingest_router.VERSIONS_DIR,
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
        yield lambda key: _As(client, _user(key) if key else None)
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def smtp(monkeypatch):
    """E-mail enabled on this node (nothing is actually sent)."""
    import notifications

    monkeypatch.setattr(notifications, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifications, "SMTP_FROM", "ged@example.com")
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://ged.example.org")


def _q(pg, sql, **params):
    from sqlalchemy import text

    async def run():
        async with pg.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.all() if result.returns_rows else []

    return asyncio.run(run())


def _tx(pg, fn):
    async def run():
        async with pg.begin() as conn:
            return await fn(conn)

    return asyncio.run(run())


_counter = [0]


def _mk(pg, *, pdf: bytes | None = None, ext="pdf", **cols) -> str:
    """A filed document with one version (a small PDF by default)."""
    import ingest_router
    from sqlalchemy import text

    _counter[0] += 1
    n = _counter[0]
    doc_id = _id()
    d = ENV["dir"] / f"doc{n}"
    d.mkdir()
    original = d / f"original.{ext}"
    original.write_bytes(pdf if pdf is not None else _pdf(f"document {n}"))
    content_hash = hashlib.sha256(original.read_bytes()).hexdigest()

    async def run():
        async with pg.begin() as conn:
            base = {
                "id": doc_id,
                "source_image_path": str(original),
                "document_type": "invoice",
                "reference_number": f"REF-{n}",
                "amount": "10",
                "review_status": "approved",
                "original_path": str(original),
                "original_hash": content_hash,
                "content_hash": content_hash,
                "record_id": f"INV-2026-{n:06d}",
            }
            if ext == "pdf":
                base["source_pdf_path"] = str(original)
            base.update(cols)
            names = ", ".join(base)
            values = ", ".join(f":{k}" for k in base)
            await conn.execute(
                text(f'INSERT INTO "{T}" ({names}) VALUES ({values})'), base
            )
            await ingest_router._insert_first_version(
                conn, TENANT["id"], T, "id = CAST(:__doc AS uuid)", {"__doc": doc_id}
            )

    asyncio.run(run())
    return doc_id


def _pdf(label: str) -> bytes:
    from pdf_report import Report

    doc = Report()
    doc.heading(label)
    doc.paragraph("Contenu de test.")
    return doc.build()


def _doc(doc_id, suffix=""):
    return f"/api/db/documents/{T}/{doc_id}{suffix}"


def _notes(pg, key, kind=None):
    sql = "SELECT kind, payload, table_name, document_id::text, read_at FROM sdai_notifications WHERE user_id = CAST(:u AS uuid)"
    params = {"u": U[key]}
    if kind:
        sql += " AND kind = :k"
        params["k"] = kind
    return _q(pg, sql + " ORDER BY created_at", **params)


def _outbox(pg, key=None):
    sql = "SELECT kind, dedupe_key, subject, body, status, locale, to_email FROM sdai_email_outbox"
    if key:
        return _q(
            pg, sql + " WHERE user_id = CAST(:u AS uuid) ORDER BY created_at", u=U[key]
        )
    return _q(pg, sql + " ORDER BY created_at")


def _audit(pg, action, doc_id=None):
    if doc_id:
        return _q(
            pg,
            "SELECT details, user_id::text FROM sdai_audit_log WHERE action = :a"
            " AND document_id = CAST(:d AS uuid)",
            a=action,
            d=doc_id,
        )
    return _q(
        pg,
        "SELECT details, user_id::text FROM sdai_audit_log WHERE action = :a",
        a=action,
    )


def _clear(pg):
    _q(pg, "DELETE FROM sdai_notifications")
    _q(pg, "DELETE FROM sdai_email_outbox")
    _q(pg, "UPDATE sdai_users SET email_notifications = 'immediate'")


def _restrict(pg, doc_id, *keys, permission="edit"):
    """Grant the document to `keys` only (it becomes restricted)."""
    for key in keys:
        _q(
            pg,
            "INSERT INTO sdai_document_access (tenant_id, table_name, document_id, grantee_type,"
            " grantee_id, scope, permission) VALUES (CAST(:t AS uuid), :tb, CAST(:d AS uuid), 'user',"
            " CAST(:u AS uuid), 'document', :p)",
            t=TENANT["id"],
            tb=T,
            d=doc_id,
            u=U[key],
            p=permission,
        )


# ── 7.1 Notifications ─────────────────────────────────────────────────────────


def test_notify_creates_rows_and_immediate_emails(pg, smtp):
    import notifications

    _clear(pg)
    _q(
        pg,
        "UPDATE sdai_users SET email_notifications = 'digest' WHERE id = CAST(:u AS uuid)",
        u=U["rev2"],
    )
    _q(
        pg,
        "UPDATE sdai_users SET email_notifications = 'off' WHERE id = CAST(:u AS uuid)",
        u=U["reader"],
    )
    doc = _mk(pg)
    ids = _tx(
        pg,
        lambda conn: notifications.notify(
            conn,
            TENANT["id"],
            [U["rev"], U["rev2"], U["reader"], U["admin"], U["admin"]],
            "mention",
            {"by": "Admin"},
            table_name=T,
            document_id=doc,
            exclude=U["admin"],
        ),
    )
    assert len(ids) == 3  # deduplicated, the actor excluded
    assert [n[0] for n in _notes(pg, "rev")] == ["mention"]
    assert _notes(pg, "admin") == []
    mails = _outbox(pg)
    assert [m[6] for m in mails] == [
        "rev@example.com"
    ]  # digest / off: no immediate mail
    kind, dedupe, subject, body, status, locale, _ = mails[0]
    assert (kind, status, locale) == ("mention", "pending", "fr")
    assert dedupe.startswith("n:")
    assert "mentionné" in subject
    # Only a link, never content (record id, reference, amounts ...).
    assert f"https://ged.example.org/documents/{T}/{doc}" in body
    assert "REF-" not in body and "INV-" not in body


def test_notify_without_smtp_queues_nothing(pg):
    import notifications

    _clear(pg)
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev"]], "lock_released", {}
        ),
    )
    assert len(_notes(pg, "rev")) == 1
    assert _outbox(pg) == []


def test_notify_coalesces_unread_and_mails_once(pg, smtp):
    import notifications

    _clear(pg)
    for _ in range(3):
        _tx(
            pg,
            lambda conn: notifications.notify(
                conn, TENANT["id"], [U["rev"]], "review_pending", {}, coalesce=True
            ),
        )
    notes = _notes(pg, "rev")
    assert len(notes) == 1 and notes[0][1]["count"] == 3
    assert len(_outbox(pg, "rev")) == 1
    _q(pg, "UPDATE sdai_notifications SET read_at = now()")
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev"]], "review_pending", {}, coalesce=True
        ),
    )
    assert len(_notes(pg, "rev")) == 2  # read ones are never bumped
    assert len(_outbox(pg, "rev")) == 2


def test_notify_never_fails_the_caller(pg):
    import notifications
    from sqlalchemy import text

    _clear(pg)

    async def run(conn):
        # A bad recipient id makes the insert fail inside the savepoint only.
        await notifications.notify(conn, TENANT["id"], ["not-a-uuid"], "mention", {})
        await conn.execute(text("SELECT 1"))  # the transaction is still usable
        return True

    assert _tx(pg, run) is True


def test_outbox_enqueue_is_idempotent(pg, smtp):
    import notifications

    _clear(pg)

    async def enqueue(conn):
        return [
            await notifications.enqueue_email(
                conn,
                tenant_id=TENANT["id"],
                user_id=U["rev"],
                to_email="rev@example.com",
                locale="en",
                kind="mention",
                dedupe_key="n:fixed",
                link="/x",
            )
            for _ in range(2)
        ]

    assert _tx(pg, enqueue) == [True, False]
    assert len(_outbox(pg)) == 1


def test_send_due_sends_retries_and_gives_up(pg, smtp, monkeypatch):
    import notifications

    _clear(pg)

    async def enqueue(conn):
        for key in ("ok", "bad"):
            await notifications.enqueue_email(
                conn,
                tenant_id=TENANT["id"],
                user_id=U["rev"],
                to_email=f"{key}@example.com",
                locale="en",
                kind="mention",
                dedupe_key=f"n:{key}",
                link="/x",
            )

    _tx(pg, enqueue)
    sent = []

    def sender(row):
        if row["to_email"].startswith("bad"):
            raise OSError("relay refused")
        sent.append(row)

    counts = asyncio.run(notifications.send_due(pg, sender=sender))
    assert counts == {"sent": 1, "retry": 1, "failed": 0}
    assert [r["to_email"] for r in sent] == ["ok@example.com"]
    rows = dict(
        (r[0], r[1:])
        for r in _q(
            pg,
            "SELECT to_email, status, attempts, last_error, next_attempt_at > now() FROM sdai_email_outbox",
        )
    )
    assert rows["ok@example.com"][:2] == ("sent", 1)
    assert rows["bad@example.com"][0] == "pending" and rows["bad@example.com"][
        2
    ].startswith("OSError")
    assert rows["bad@example.com"][3] is True  # backed off
    # Not due yet: nothing happens; the sent one is never sent again.
    assert asyncio.run(notifications.send_due(pg, sender=sender)) == {
        "sent": 0,
        "retry": 0,
        "failed": 0,
    }
    # Last attempt -> failed for good.
    monkeypatch.setattr(notifications, "EMAIL_MAX_ATTEMPTS", 2)
    _q(pg, "UPDATE sdai_email_outbox SET next_attempt_at = now() - interval '1 second'")
    assert asyncio.run(notifications.send_due(pg, sender=sender)) == {
        "sent": 0,
        "retry": 0,
        "failed": 1,
    }
    assert len(sent) == 1


def test_send_due_reclaims_stale_sending_rows(pg, smtp):
    import notifications

    _clear(pg)
    _tx(
        pg,
        lambda conn: notifications.enqueue_email(
            conn,
            tenant_id=None,
            user_id=None,
            to_email="x@example.com",
            locale="en",
            kind="mention",
            dedupe_key="n:stale",
            link="/x",
        ),
    )
    _q(
        pg,
        "UPDATE sdai_email_outbox SET status = 'sending', claimed_at = now() - interval '1 hour'",
    )
    sent = []
    asyncio.run(notifications.send_due(pg, sender=sent.append))
    assert len(sent) == 1
    assert _q(pg, "SELECT status FROM sdai_email_outbox")[0][0] == "sent"


def test_daily_digest_once_per_day(pg, smtp):
    import notifications

    _clear(pg)
    _q(
        pg,
        "UPDATE sdai_users SET email_notifications = 'digest' WHERE id = CAST(:u AS uuid)",
        u=U["rev2"],
    )
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev2"], U["rev"]], "mention", {}
        ),
    )
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev2"]], "lock_released", {}
        ),
    )
    assert asyncio.run(notifications.build_digests(pg, date(2026, 10, 14))) == 1
    assert asyncio.run(notifications.build_digests(pg, date(2026, 10, 14))) == 0
    digest = [m for m in _outbox(pg, "rev2") if m[0] == "digest"]
    assert len(digest) == 1
    assert (
        "(1)" in digest[0][3]
        and "https://ged.example.org/notifications" in digest[0][3]
    )
    assert asyncio.run(notifications.build_digests(pg, date(2026, 10, 15))) == 1


def test_notifications_api(pg, as_user):
    import notifications

    _clear(pg)
    doc = _mk(pg)
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn,
            TENANT["id"],
            [U["rev"]],
            "mention",
            {"record_id": "INV-1", "by": "Admin"},
            table_name=T,
            document_id=doc,
        ),
    )
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev"], U["rev2"]], "lock_released", {}
        ),
    )
    rev = as_user("rev")
    assert rev.get("/api/db/notifications/unread-count").json() == {"count": 2}
    items = rev.get("/api/db/notifications").json()
    assert [i["kind"] for i in items] == ["lock_released", "mention"]
    mention = items[1]
    assert (
        mention["link"] == f"/documents/{T}/{doc}"
        and mention["payload"]["record_id"] == "INV-1"
    )
    # Somebody else's notification can't be marked read.
    other = _q(
        pg,
        "SELECT id::text FROM sdai_notifications WHERE user_id = CAST(:u AS uuid)",
        u=U["rev2"],
    )[0][0]
    rev.post(f"/api/db/notifications/{other}/read")
    assert (
        _q(
            pg,
            "SELECT read_at FROM sdai_notifications WHERE id = CAST(:i AS uuid)",
            i=other,
        )[0][0]
        is None
    )
    assert rev.post(f"/api/db/notifications/{mention['id']}/read").status_code == 200
    assert rev.get("/api/db/notifications/unread-count").json() == {"count": 1}
    assert [
        i["kind"] for i in rev.get("/api/db/notifications?unread_only=true").json()
    ] == ["lock_released"]
    assert rev.post("/api/db/notifications/read-all").json()["updated"] == 1
    assert rev.get("/api/db/notifications/unread-count").json() == {"count": 0}
    assert as_user("rev2").get("/api/db/notifications/unread-count").json() == {
        "count": 1
    }
    assert rev.post("/api/db/notifications/garbage/read").status_code == 200


def test_notification_about_unreadable_document_is_stripped(pg, as_user):
    import notifications

    _clear(pg)
    doc = _mk(pg)
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn,
            TENANT["id"],
            [U["rev"]],
            "document_shared",
            {"record_id": "INV-SECRET", "by": "Admin"},
            table_name=T,
            document_id=doc,
        ),
    )
    _restrict(pg, doc, "editor")  # rev loses access
    item = as_user("rev").get("/api/db/notifications").json()[0]
    assert item["document_id"] is None and item["table_name"] is None
    assert "record_id" not in item["payload"] and item["payload"]["unavailable"] is True
    assert item["link"] == "/notifications"


def test_preferences(pg, as_user):
    rev2 = as_user("rev2")
    prefs = rev2.get("/api/db/notification-preferences").json()
    assert prefs["email_notifications"] in ("immediate", "digest", "off")
    r = rev2.put(
        "/api/db/notification-preferences",
        json={"email_notifications": "off", "locale": "en"},
    )
    assert (
        r.status_code == 200
        and r.json()["email_notifications"] == "off"
        and r.json()["locale"] == "en"
    )
    assert (
        rev2.put(
            "/api/db/notification-preferences",
            json={"email_notifications": "sometimes"},
        ).status_code
        == 422
    )
    assert as_user(None).get("/api/db/notification-preferences").status_code == 401
    assert _audit(pg, "notification_preferences_changed")


def test_grant_notifies_the_grantee(pg, as_user, smtp):
    _clear(pg)
    doc = _mk(pg)
    r = as_user("admin").post(
        _doc(doc, "/access"),
        json={"grantee_type": "user", "grantee_id": U["rev"], "permission": "read"},
    )
    assert r.status_code == 200, r.text
    notes = _notes(pg, "rev", "document_shared")
    assert len(notes) == 1 and notes[0][3] == doc and notes[0][1]["by"] == "Admin"
    assert _notes(pg, "admin") == []
    assert len(_outbox(pg, "rev")) == 1


def test_review_pending_goes_to_people_who_can_review(pg):
    import notifications

    _clear(pg)
    doc = _mk(pg, review_status="review_required")
    _restrict(pg, doc, "rev", "reader", permission="edit")
    asyncio.run(notifications.notify_review_pending(TENANT["id"], T, doc))
    asyncio.run(notifications.notify_review_pending(TENANT["id"], T, doc))
    assert _notes(pg, "rev")[0][1]["count"] == 2
    assert _notes(pg, "admin") and _notes(pg, "admin2")
    assert _notes(pg, "reader") == []  # never reviews
    assert _notes(pg, "rev2") == []  # not granted


def test_ops_alerts_notify_admins_once(pg):
    import notifications

    _clear(pg)
    _q(pg, "DELETE FROM sdai_backup_runs")
    _q(pg, "DELETE FROM sdai_ops_alerts")
    _q(
        pg,
        "INSERT INTO sdai_backup_runs (node_id, started_at, status, target) VALUES ('n', now(), 'failed', 'sftp')",
    )
    assert asyncio.run(notifications.check_ops_alerts(pg)) == ["backup_failed"]
    assert asyncio.run(notifications.check_ops_alerts(pg)) == []
    assert [n[0] for n in _notes(pg, "admin")] == ["backup_failed"]
    assert _notes(pg, "rev") == []
    _q(
        pg,
        "INSERT INTO sdai_backup_runs (node_id, started_at, finished_at, status, target) VALUES ('n', now(), now() + interval '1 second', 'success', 'sftp')",
    )
    asyncio.run(notifications.check_ops_alerts(pg))
    assert _q(pg, "SELECT count(*) FROM sdai_ops_alerts")[0][0] == 0


# ── 7.2 Check-out / check-in ──────────────────────────────────────────────────


def _upload(client, doc, name="v.pdf", content=None, path="/versions", **form):
    return client.post(
        _doc(doc, path),
        files={
            "file": (name, content or _pdf(f"new version {_id()}"), "application/pdf")
        },
        data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
    )


def _write_paths(doc, other):
    """Every edit-level write on a document (method, path, kwargs)."""
    return [
        ("patch", _doc(doc, "/fields"), {"json": {"fields": {"amount": "99"}}}),
        (
            "post",
            _doc(doc, "/links"),
            {"json": {"to_table": T, "to_id": other, "relation": "related"}},
        ),
        ("patch", _doc(doc, "/series"), {"json": {"series_id": None}}),
        ("post", _doc(doc, "/tags"), {"json": {"name": "urgent"}}),
        ("post", _doc(doc, "/versions/1/restore"), {"json": {}}),
        (
            "patch",
            f"/api/review/{T}/{doc}",
            {"json": {"action": "approve", "fields": {}}},
        ),
        ("post", f"/api/review/{T}/{doc}/flag", {"json": {}}),
        ("delete", f"/api/review/{T}/{doc}", {}),
        ("post", _doc(doc, "/trash"), {"json": {}}),
        ("post", _doc(doc, "/archive"), {}),
    ]


def test_checkout_blocks_every_write_path_for_others(pg, as_user):
    doc, other = _mk(pg), _mk(pg)
    editor = as_user("editor")
    r = editor.post(_doc(doc, "/checkout"), json={"hours": 2})
    assert r.status_code == 200, r.text
    lock = r.json()
    assert lock["locked"] and lock["mine"] and lock["can_checkin"]
    assert _audit(pg, "document_checked_out", doc)[0][0]["hours"] == 2

    view = as_user("rev").get(_doc(doc, "/lock")).json()
    assert view["locked"] and not view["mine"] and view["locked_by_name"] == "Editor"
    assert not view["can_checkout"] and not view["can_force_unlock"]
    assert as_user("admin").get(_doc(doc, "/lock")).json()["can_force_unlock"]

    for key in (
        "archivist",
        "admin",
    ):  # field-editors - nothing else refuses them first
        client = as_user(key)
        for method, path, kwargs in _write_paths(doc, other):
            resp = getattr(client, method)(path, **kwargs)
            assert resp.status_code == 423, (
                key,
                method,
                path,
                resp.status_code,
                resp.text,
            )
        assert _upload(client, doc).status_code == 423
        assert client.post(_doc(doc, "/checkout")).status_code == 423
    assert (
        "Editor"
        in as_user("admin")
        .patch(_doc(doc, "/fields"), json={"fields": {"amount": "1"}})
        .json()["detail"]
    )
    assert (
        as_user("rev").post(_doc(doc, "/tags"), json={"name": "x"}).status_code == 423
    )
    # Reads still work for everyone.
    assert as_user("rev").get(_doc(doc)).status_code == 200
    # The holder still edits.
    assert (
        editor.patch(
            _doc(doc, "/fields"), json={"fields": {"amount": "42"}}
        ).status_code
        == 200
    )
    assert _upload(editor, doc).status_code == 200


def test_checkin_with_new_version_releases(pg, as_user):
    doc = _mk(pg)
    editor = as_user("editor")
    assert editor.post(_doc(doc, "/checkout")).status_code == 200
    until = _q(
        pg, f'SELECT locked_until - now() FROM "{T}" WHERE id = CAST(:d AS uuid)', d=doc
    )[0][0]
    assert timedelta(hours=7, minutes=59) < until <= timedelta(hours=8)  # default 8 h
    assert (
        as_user("rev").post(_doc(doc, "/checkin")).status_code == 409
    )  # not the holder
    r = _upload(editor, doc, path="/checkin", comment="corrigé")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["locked"] and body["version"]["version_no"] == 2
    assert body["version"]["comment"] == "corrigé"
    row = _q(
        pg,
        f'SELECT locked_by, locked_until, current_version FROM "{T}" WHERE id = CAST(:d AS uuid)',
        d=doc,
    )[0]
    assert row == (None, None, 2)
    details = _audit(pg, "document_checked_in", doc)[0][0]
    assert details["reason"] == "checkin" and details["version_no"] == 2
    assert (
        as_user("archivist")
        .patch(_doc(doc, "/fields"), json={"fields": {"amount": "5"}})
        .status_code
        == 200
    )
    assert editor.post(_doc(doc, "/checkin")).status_code == 409  # nothing to check in


def test_checkin_without_file_and_extend(pg, as_user):
    doc = _mk(pg)
    editor = as_user("editor")
    editor.post(_doc(doc, "/checkout"), json={"hours": 1})
    r = editor.post(_doc(doc, "/checkout"), json={"hours": 5})  # extend
    assert r.status_code == 200
    assert _audit(pg, "document_checked_out", doc)[-1][0]["extended"] is True
    r = editor.post(_doc(doc, "/checkin"), data={"comment": "rien"})
    assert r.status_code == 200 and r.json()["version"] is None
    assert (
        _q(pg, f'SELECT current_version FROM "{T}" WHERE id = CAST(:d AS uuid)', d=doc)[
            0
        ][0]
        == 1
    )


def test_checkout_needs_edit(pg, as_user):
    doc = _mk(pg)
    assert as_user("reader").post(_doc(doc, "/checkout")).status_code == 403
    _restrict(pg, doc, "editor")
    assert (
        as_user("rev").post(_doc(doc, "/checkout")).status_code == 404
    )  # can't even see it
    archived = _mk(pg, lifecycle_status="archived")
    assert (
        as_user("editor").post(_doc(archived, "/checkout")).status_code == 403
    )  # read-only
    assert as_user("archivist").post(_doc(archived, "/checkout")).status_code == 200


def test_force_unlock_notifies_holder(pg, as_user):
    _clear(pg)
    doc = _mk(pg)
    as_user("editor").post(_doc(doc, "/checkout"))
    assert (
        as_user("archivist").post(_doc(doc, "/unlock")).status_code == 403
    )  # admin only
    r = as_user("admin").post(_doc(doc, "/unlock"))
    assert r.status_code == 200 and not r.json()["locked"]
    notes = _notes(pg, "editor", "lock_released")
    assert len(notes) == 1 and notes[0][3] == doc and notes[0][1]["by"] == "Admin"
    details = [d for d, _ in _audit(pg, "document_checked_in", doc)]
    assert details[-1]["reason"] == "forced" and details[-1]["holder"] == U["editor"]
    assert as_user("admin").post(_doc(doc, "/unlock")).status_code == 409


def test_expired_lock_is_free_and_swept(pg, as_user):
    import locks

    doc = _mk(pg)
    _q(
        pg,
        f"UPDATE \"{T}\" SET locked_by = CAST(:u AS uuid), locked_until = now() - interval '1 minute'"
        " WHERE id = CAST(:d AS uuid)",
        u=U["editor"],
        d=doc,
    )
    assert not as_user("rev").get(_doc(doc, "/lock")).json()["locked"]
    assert (
        as_user("archivist")
        .patch(_doc(doc, "/fields"), json={"fields": {"amount": "7"}})
        .status_code
        == 200
    )
    assert asyncio.run(locks.expire_locks(pg)) >= 1
    assert (
        _q(pg, f'SELECT locked_by FROM "{T}" WHERE id = CAST(:d AS uuid)', d=doc)[0][0]
        is None
    )
    assert _audit(pg, "document_checked_in", doc)[-1][0]["reason"] == "expired"


def test_locked_document_leaves_others_review_queue(pg, as_user):
    doc = _mk(pg, review_status="review_required")

    def queued(key):
        return any(
            i.get("id") == doc
            for i in as_user(key).get("/api/review/queue").json()["items"]
        )

    assert queued("rev")
    as_user("editor").post(_doc(doc, "/checkout"))
    assert not queued("rev") and queued("editor")


# ── 7.3 Comments ──────────────────────────────────────────────────────────────


def _comments(doc):
    return _doc(doc, "/comments")


def test_comments_post_read_and_rights(pg, as_user):
    doc = _mk(pg)
    rev = as_user("rev")  # reviewer without field-edit rights may still comment
    r = rev.post(_comments(doc), json={"body": "  Montant à vérifier  "})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["body"] == "Montant à vérifier" and c["version_no"] == 1 and c["can_edit"]
    assert as_user("reader").post(_comments(doc), json={"body": "x"}).status_code == 403
    assert rev.post(_comments(doc), json={"body": "   "}).status_code == 422
    listed = as_user("reader").get(_comments(doc)).json()
    assert [x["body"] for x in listed] == ["Montant à vérifier"]
    assert not listed[0]["can_edit"] and not listed[0]["can_delete"]
    audit = _audit(pg, "comment_added", doc)[0][0]
    assert audit["comment_id"] == c["id"] and audit["length"] == 18
    assert "Montant" not in json.dumps(audit)  # the text stays out of the audit log


def test_comments_on_locked_and_archived_documents(pg, as_user):
    locked = _mk(pg)
    as_user("editor").post(_doc(locked, "/checkout"))
    assert (
        as_user("rev").post(_comments(locked), json={"body": "ok"}).status_code == 201
    )
    archived = _mk(pg, lifecycle_status="archived")
    assert (
        as_user("rev").post(_comments(archived), json={"body": "ok"}).status_code == 201
    )


def test_comment_leaks(pg, as_user):
    doc = _mk(pg)
    c = as_user("editor").post(_comments(doc), json={"body": "secret note"}).json()
    _restrict(pg, doc, "editor")
    rev = as_user("rev")
    assert rev.get(_comments(doc)).status_code == 404
    assert rev.post(_comments(doc), json={"body": "x"}).status_code == 404
    assert (
        rev.patch(f"/api/db/comments/{c['id']}", json={"body": "x"}).status_code == 404
    )
    assert rev.delete(f"/api/db/comments/{c['id']}").status_code == 404
    assert rev.get(_doc(doc, "/comments/mentionable")).status_code == 404
    assert rev.delete("/api/db/comments/not-a-uuid").status_code == 404
    trashed = _mk(pg, deleted_at=datetime.now(timezone.utc))
    assert as_user("admin").get(_comments(trashed)).status_code == 404


def test_mentions_notify_readers_only(pg, as_user, smtp):
    _clear(pg)
    doc = _mk(pg)
    _restrict(pg, doc, "editor", "rev")
    editor = as_user("editor")
    mentionable = editor.get(_doc(doc, "/comments/mentionable")).json()
    names = {m["name"] for m in mentionable}
    assert (
        "Rev" in names
        and "Admin" in names
        and "Rev2" not in names
        and "Editor" not in names
    )
    assert {
        m["name"] for m in editor.get(_doc(doc, "/comments/mentionable?q=adm")).json()
    } == {"Admin", "Admin2"}
    r = editor.post(
        _comments(doc),
        json={
            "body": "@Rev @Rev2 regardez",
            "mentions": [U["rev"], U["rev2"], U["editor"], "junk"],
        },
    )
    c = r.json()
    # rev2 can't read the document and "junk" is no user: both dropped.
    assert {m["id"] for m in c["mentions"]} == {U["rev"], U["editor"]}
    stored = _q(
        pg, "SELECT mentions FROM sdai_comments WHERE id = CAST(:i AS uuid)", i=c["id"]
    )[0][0]
    assert U["rev2"] not in stored and U["rev"] in stored  # rev2 can't read it
    notes = _notes(pg, "rev", "mention")
    assert len(notes) == 1 and notes[0][3] == doc and notes[0][1]["by"] == "Editor"
    assert _notes(pg, "rev2") == [] and _notes(pg, "editor") == []  # nor the author
    mail = _outbox(pg, "rev")[0]
    assert "regardez" not in mail[3]  # no comment text in e-mails


def test_reply_notifies_parent_author(pg, as_user):
    _clear(pg)
    doc = _mk(pg)
    parent = as_user("rev").post(_comments(doc), json={"body": "question"}).json()
    r = as_user("editor").post(
        _comments(doc), json={"body": "réponse", "parent_id": parent["id"]}
    )
    assert r.status_code == 201 and r.json()["parent_id"] == parent["id"]
    assert len(_notes(pg, "rev", "comment_reply")) == 1
    other = _mk(pg)
    bad = as_user("editor").post(
        _comments(other), json={"body": "x", "parent_id": parent["id"]}
    )
    assert bad.status_code == 422  # the parent belongs to another document


def test_edit_and_delete_comment(pg, as_user):
    _clear(pg)
    doc = _mk(pg)
    c = as_user("rev").post(_comments(doc), json={"body": "v1"}).json()
    url = f"/api/db/comments/{c['id']}"
    assert as_user("editor").patch(url, json={"body": "hack"}).status_code == 403
    r = as_user("rev").patch(url, json={"body": "v2", "mentions": [U["editor"]]})
    assert r.status_code == 200 and r.json()["body"] == "v2" and r.json()["edited_at"]
    assert len(_notes(pg, "editor", "mention")) == 1
    as_user("rev").patch(
        url, json={"body": "v3"}
    )  # mentions unchanged - no new notification
    assert len(_notes(pg, "editor", "mention")) == 1
    assert as_user("editor").delete(url).status_code == 403
    assert as_user("admin").delete(url).status_code == 200
    listed = as_user("rev").get(_comments(doc)).json()
    assert listed[0]["deleted"] and listed[0]["body"] is None
    assert as_user("rev").patch(url, json={"body": "v4"}).status_code == 403
    assert _audit(pg, "comment_edited", doc) and _audit(pg, "comment_deleted", doc)


def test_destruction_removes_comments_and_notifications(pg, as_user):
    import destruction
    import notifications

    doc = _mk(pg)
    as_user("rev").post(_comments(doc), json={"body": "à détruire"})
    _tx(
        pg,
        lambda conn: notifications.notify(
            conn, TENANT["id"], [U["rev"]], "mention", {}, table_name=T, document_id=doc
        ),
    )
    _q(pg, f'UPDATE "{T}" SET deleted_at = now() WHERE id = CAST(:d AS uuid)', d=doc)

    async def destroy(conn):
        await destruction.execute(
            conn,
            TENANT["id"],
            "default",
            _id(),
            [{"table_name": T, "id": doc}],
            "purge",
        )

    _tx(pg, destroy)
    assert (
        _q(
            pg, f'SELECT lifecycle_status FROM "{T}" WHERE id = CAST(:d AS uuid)', d=doc
        )[0][0]
        == "destroyed"
    )
    assert (
        _q(
            pg,
            "SELECT count(*) FROM sdai_comments WHERE document_id = CAST(:d AS uuid)",
            d=doc,
        )[0][0]
        == 0
    )
    assert (
        _q(
            pg,
            "SELECT count(*) FROM sdai_notifications WHERE document_id = CAST(:d AS uuid)",
            d=doc,
        )[0][0]
        == 0
    )


# ── 7.4 Electronic signature ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def p12(tmp_path_factory):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import generate_signing_cert

    path = tmp_path_factory.mktemp("cert") / "org.p12"
    path.write_bytes(
        generate_signing_cert.build(
            "Test Org", "Test Org - signature", "", 1, "secret123"
        )
    )
    return path


@pytest.fixture()
def signing(p12, monkeypatch):
    import signatures

    monkeypatch.setattr(signatures, "SIGNING_P12_PATH", str(p12))
    monkeypatch.setattr(signatures, "SIGNING_P12_PASSWORD", "secret123")
    signatures._load.cache_clear()
    yield
    signatures._load.cache_clear()


def _ask(client, doc, *keys, reason="Approbation", titles=None):
    return client.post(
        _doc(doc, "/signature-requests"),
        json={
            "signers": [
                {"user_id": U[k], **({"title": titles[i]} if titles else {})}
                for i, k in enumerate(keys)
            ],
            "reason": reason,
        },
    )


def _sign(client, request_id, password="secret-pw", **extra):
    return client.post(
        f"/api/db/signature-requests/{request_id}/sign",
        json={"password": password, **extra},
    )


def test_signing_status(pg, as_user, signing):
    st = as_user("rev").get("/api/db/signing/status").json()
    assert st["configured"] and st["self_signed"] and "Test Org" in st["subject"]


def test_signing_not_configured(pg, as_user, monkeypatch):
    import signatures

    monkeypatch.setattr(signatures, "SIGNING_P12_PATH", "")
    doc = _mk(pg)
    req = _ask(as_user("editor"), doc, "rev").json()
    assert _sign(as_user("rev"), req["id"]).status_code == 503
    assert as_user("rev").get("/api/db/signing/status").json()["configured"] is False


def test_sign_creates_new_valid_version(pg, as_user, signing):
    import signatures

    _clear(pg)
    doc = _mk(pg)
    r = _ask(
        as_user("editor"), doc, "rev", "rev2", titles=["Chef de service", "Directeur"]
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert [s["name"] for s in req["signers"]] == ["Rev", "Rev2"] and req["signers"][0][
        "is_next"
    ]
    assert _audit(pg, "signature_requested", doc)
    assert [n[0] for n in _notes(pg, "rev")] == ["signature_requested"]
    assert _notes(pg, "rev2") == []  # not their turn yet
    # Out of order, wrong password: refused, nothing changes.
    assert _sign(as_user("rev2"), req["id"]).status_code == 403
    assert _sign(as_user("rev"), req["id"], password="wrong").status_code == 403
    assert _audit(pg, "signature_password_failed", doc)
    assert (
        _q(pg, f'SELECT current_version FROM "{T}" WHERE id = CAST(:d AS uuid)', d=doc)[
            0
        ][0]
        == 1
    )
    assert req["id"] in [
        x["id"] for x in as_user("rev").get("/api/db/signature-requests/pending").json()
    ]
    assert req["id"] not in [
        x["id"]
        for x in as_user("rev2").get("/api/db/signature-requests/pending").json()
    ]

    r = _sign(as_user("rev"), req["id"], reason="Visa")
    assert r.status_code == 200, r.text
    first = r.json()
    assert (
        first["version"]["version_no"] == 2 and first["request"]["status"] == "pending"
    )
    assert [n[0] for n in _notes(pg, "rev2")] == ["signature_requested"]
    r = _sign(as_user("rev2"), req["id"])
    assert r.status_code == 200, r.text
    assert (
        r.json()["version"]["version_no"] == 3
        and r.json()["request"]["status"] == "completed"
    )
    assert [n[0] for n in _notes(pg, "editor")] == ["signature_completed"]

    # The signed file is the current version and both signatures validate.
    path, file_hash = _q(
        pg,
        "SELECT file_path, file_hash FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid) AND version_no = 3",
        d=doc,
    )[0]
    from pathlib import Path

    data = Path(path).read_bytes()
    assert hashlib.sha256(data).hexdigest() == file_hash
    checks = signatures.verify_pdf(data)
    assert [c["signer_name"] for c in checks] == ["Rev", "Rev2"]
    assert all(c["ok"] for c in checks) and checks[0]["reason"] == "Visa"

    v = as_user("reader").get(_doc(doc, "/signatures")).json()
    assert v["level"] == "simple"
    assert v["verification"]["all_ok"] and v["verification"]["file_hash_ok"]
    assert v["verification"]["version_no"] == 3
    assert all(s["evidence_matches"] for s in v["verification"]["signatures"])
    ev = v["evidence"]
    assert [e["signer_title"] for e in ev] == ["Chef de service", "Directeur"]
    assert ev[1]["source_version_no"] == 2 and ev[1]["signed_file_hash"] == file_hash
    assert {a["action"] for a in v["audit"]} >= {
        "signature_requested",
        "document_signed",
    }
    # The unsigned original verifies as "no signature".
    v1 = (
        as_user("reader").get(_doc(doc, "/signatures?version=1")).json()["verification"]
    )
    assert v1["signatures"] == [] and not v1["all_ok"]
    assert _sign(as_user("rev2"), req["id"]).status_code == 409  # closed


def test_sign_image_original_converts_first(pg, as_user, signing):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, format="PNG")
    doc = _mk(pg, pdf=buf.getvalue(), ext="png")
    req = _ask(as_user("editor"), doc, "rev").json()
    r = _sign(as_user("rev"), req["id"])
    assert r.status_code == 200, r.text
    rows = _q(
        pg,
        "SELECT version_no, mime, comment FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid) ORDER BY version_no",
        d=doc,
    )
    assert [(n, m) for n, m, _ in rows] == [
        (1, "image/png"),
        (2, "application/pdf"),
        (3, "application/pdf"),
    ]
    assert "PDF" in rows[1][2]
    details = _audit(pg, "document_signed", doc)[0][0]
    assert details["converted_version_no"] == 2 and details["source_version_no"] == 2
    assert as_user("rev").get(_doc(doc, "/signatures")).json()["verification"]["all_ok"]


def test_tampered_signed_file_fails_verification(pg, as_user, signing):
    from pathlib import Path

    doc = _mk(pg)
    req = _ask(as_user("editor"), doc, "rev").json()
    _sign(as_user("rev"), req["id"])
    path = _q(
        pg,
        "SELECT file_path FROM sdai_document_versions WHERE document_id = CAST(:d AS uuid) AND version_no = 2",
        d=doc,
    )[0][0]
    data = bytearray(Path(path).read_bytes())
    i = data.find(b"Contenu")
    data[i] = ord("K")
    Path(path).write_bytes(bytes(data))
    v = as_user("rev").get(_doc(doc, "/signatures")).json()["verification"]
    assert not v["all_ok"] and not v["file_hash_ok"]
    assert v["signatures"][0]["intact"] is False


def test_signature_request_rules(pg, as_user, signing):
    doc = _mk(pg)
    assert _ask(as_user("reader"), doc, "rev").status_code == 403  # needs edit
    _restrict(pg, doc, "editor", "rev")
    assert _ask(as_user("editor"), doc, "rev2").status_code == 422  # rev2 can't read it
    assert _ask(as_user("editor"), doc, "rev", "rev").status_code == 422  # twice
    r = as_user("editor").post(
        _doc(doc, "/signature-requests"),
        json={"signers": [{"user_id": "x"}], "reason": "ok"},
    )
    assert r.status_code == 422
    req = _ask(as_user("editor"), doc, "rev").json()
    assert (
        _ask(as_user("editor"), doc, "rev").status_code == 409
    )  # one pending request at a time
    assert as_user("rev2").get(_doc(doc, "/signature-requests")).status_code == 404
    assert (
        as_user("rev2")
        .post(f"/api/db/signature-requests/{req['id']}/cancel")
        .status_code
        == 404
    )
    assert _sign(as_user("rev2"), req["id"]).status_code == 404
    listed = as_user("rev").get(_doc(doc, "/signature-requests")).json()
    assert listed[0]["can_sign"] and not listed[0]["can_cancel"]
    assert (
        as_user("rev")
        .post(f"/api/db/signature-requests/{req['id']}/cancel")
        .status_code
        == 403
    )
    r = as_user("editor").post(
        f"/api/db/signature-requests/{req['id']}/cancel", json={"note": "erreur"}
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _sign(as_user("rev"), req["id"]).status_code == 409
    assert _audit(pg, "signature_request_cancelled", doc)


def test_decline_notifies_requester(pg, as_user, signing):
    _clear(pg)
    doc = _mk(pg)
    req = _ask(as_user("editor"), doc, "rev", "rev2").json()
    assert (
        as_user("rev2")
        .post(f"/api/db/signature-requests/{req['id']}/decline")
        .status_code
        == 403
    )
    r = as_user("rev").post(
        f"/api/db/signature-requests/{req['id']}/decline", json={"note": "montant faux"}
    )
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert [n[0] for n in _notes(pg, "editor")] == ["signature_declined"]
    assert _notes(pg, "rev2") == []
    assert _audit(pg, "signature_declined", doc)[0][0]["note"] == "montant faux"


def test_signing_respects_lock_and_lifecycle(pg, as_user, signing):
    doc = _mk(pg)
    req = _ask(as_user("editor"), doc, "rev").json()
    as_user("editor").post(_doc(doc, "/checkout"))
    assert (
        _sign(as_user("rev"), req["id"]).status_code == 409
    )  # checked out by someone else
    as_user("editor").post(_doc(doc, "/checkin"))
    _q(
        pg,
        f"UPDATE \"{T}\" SET lifecycle_status = 'archived' WHERE id = CAST(:d AS uuid)",
        d=doc,
    )
    assert _sign(as_user("rev"), req["id"]).status_code == 403  # read-only
    _q(
        pg,
        f"UPDATE \"{T}\" SET lifecycle_status = 'active' WHERE id = CAST(:d AS uuid)",
        d=doc,
    )
    assert _sign(as_user("rev"), req["id"]).status_code == 200
    # A reader can be asked to sign, too.
    doc2 = _mk(pg)
    req2 = _ask(as_user("editor"), doc2, "reader").json()
    assert _sign(as_user("reader"), req2["id"]).status_code == 200


def test_destruction_removes_signatures(pg, as_user, signing):
    import destruction

    doc = _mk(pg)
    req = _ask(as_user("editor"), doc, "rev").json()
    _sign(as_user("rev"), req["id"])
    _q(pg, f'UPDATE "{T}" SET deleted_at = now() WHERE id = CAST(:d AS uuid)', d=doc)

    async def destroy(conn):
        await destruction.execute(
            conn,
            TENANT["id"],
            "default",
            _id(),
            [{"table_name": T, "id": doc}],
            "purge",
        )

    _tx(pg, destroy)
    for table in ("sdai_signatures", "sdai_signature_requests"):
        assert (
            _q(
                pg,
                f"SELECT count(*) FROM {table} WHERE document_id = CAST(:d AS uuid)",
                d=doc,
            )[0][0]
            == 0
        )


def test_admin_email_outbox_screen(pg, as_user, smtp):
    import notifications

    _clear(pg)

    async def enqueue(conn):
        for key in ("ok", "bad", "other"):
            await notifications.enqueue_email(
                conn,
                tenant_id=TENANT["id"] if key != "other" else None,
                user_id=U["rev"],
                to_email=f"{key}@example.com",
                locale="en",
                kind="mention",
                dedupe_key=f"n:admin-{key}",
                link="/x",
            )

    _tx(pg, enqueue)
    _q(
        pg,
        "UPDATE sdai_email_outbox SET status = 'failed', attempts = 8, last_error = 'relay refused' WHERE to_email <> 'ok@example.com'",
    )
    # Another tenant's failed e-mail is never shown nor touched.
    other_tenant = _q(
        pg,
        "INSERT INTO sdai_tenants (slug, name) VALUES ('othermail', 'Other') RETURNING id::text",
    )[0][0]
    _q(
        pg,
        "INSERT INTO sdai_email_outbox (tenant_id, to_email, kind, dedupe_key, subject, body, status) VALUES (CAST(:t AS uuid), 'x@other.org', 'mention', 'n:foreign', 's', 'b', 'failed')",
        t=other_tenant,
    )

    assert as_user("rev").get("/api/admin/emails").status_code == 403
    admin = as_user("admin")
    data = admin.get("/api/admin/emails").json()
    assert data["email_enabled"] is True
    assert sorted(i["to_email"] for i in data["items"]) == [
        "bad@example.com",
        "other@example.com",
    ]
    assert data["counts"]["failed"] == 2 and data["counts"]["pending"] == 1
    assert "relay refused" in data["items"][0]["last_error"]
    alerts = admin.get("/api/admin/sync/status").json()["alerts"]
    assert {"kind": "emails_failed", "count": 2} in alerts

    bad_id = next(i["id"] for i in data["items"] if i["to_email"] == "bad@example.com")
    assert admin.post(
        "/api/admin/emails/retry", json={"ids": [bad_id, "junk"]}
    ).json() == {"retried": 1}
    row = _q(
        pg,
        "SELECT status, attempts, last_error FROM sdai_email_outbox WHERE id = CAST(:i AS uuid)",
        i=bad_id,
    )[0]
    assert row == ("pending", 0, None)
    assert admin.post("/api/admin/emails/discard").json() == {
        "discarded": 1
    }  # other@example.com
    assert (
        _q(pg, "SELECT status FROM sdai_email_outbox WHERE dedupe_key = 'n:foreign'")[
            0
        ][0]
        == "failed"
    )
    assert _audit(pg, "emails_retried") and _audit(pg, "emails_discarded")
    _q(pg, "DELETE FROM sdai_email_outbox WHERE dedupe_key = 'n:foreign'")
    _q(pg, "DELETE FROM sdai_tenants WHERE slug = 'othermail'")
