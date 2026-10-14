"""Notifications (GED Phase 7.1) - in-app list + e-mail outbox.

    notify()            one in-app row per recipient in sdai_notifications
                        (synced, so the bell shows the same list on both
                        nodes) and, for recipients who chose "immediate",
                        one row in the e-mail outbox. Runs in the caller's
                        transaction under a savepoint and never raises: a
                        notification is never worth failing the action.
    sdai_email_outbox   node-local: the node that created an e-mail is the
                        one that sends it (so a cloud-only notification
                        still goes out while the local node is offline, and
                        no e-mail is ever sent twice by two nodes). One row
                        per dedupe_key - enqueueing the same e-mail twice is
                        a no-op.
    email_worker()      every node: sends due outbox rows over SMTP_*,
                        retries with backoff, gives up after
                        EMAIL_MAX_ATTEMPTS. Delivery is at-least-once (a
                        crash between the SMTP answer and the status update
                        resends; the Message-ID is the outbox id, stable
                        across retries).
    digest_worker()     local node only: once a day, one e-mail per
                        "digest" user summarising their unread
                        notifications of the last 24 h.
    ops_alert_worker()  every node: notifies the admins once when this
                        node's sync is down or a backup failed (again only
                        after the alert cleared and came back).

E-mails never carry document content: a fixed sentence per kind and a link
to PUBLIC_BASE_URL, in the recipient's language (sdai_users.locale).

No router imports at module level (ingest_router imports this module).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate

from i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, t
from sqlalchemy import text

logger = logging.getLogger(__name__)

KINDS = (
    "review_pending",
    "document_shared",
    "mention",
    "comment_reply",
    "retention_eligible",
    "destruction_proposed",
    "sync_down",
    "backup_failed",
    "signature_requested",
    "signature_completed",
    "signature_declined",
    "lock_released",
)

EMAIL_PREFERENCES = ("immediate", "digest", "off")

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
# "starttls" (port 587), "ssl" (implicit TLS, port 465) or "none" (a relay
# on the local network).
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

EMAIL_POLL_SECONDS = int(os.getenv("EMAIL_POLL_SECONDS", "30"))
EMAIL_MAX_ATTEMPTS = int(os.getenv("EMAIL_MAX_ATTEMPTS", "8"))
EMAIL_DIGEST_HOUR = int(os.getenv("EMAIL_DIGEST_HOUR", "7"))
OPS_ALERT_CHECK_SECONDS = int(os.getenv("OPS_ALERT_CHECK_SECONDS", "300"))
# A 'sending' row older than this was claimed by a process that died.
_STALE_SENDING = timedelta(minutes=10)

# Kinds an ops alert can raise (sync.admin.compute_alerts kinds).
OPS_ALERT_KINDS = ("sync_down", "backup_failed")


def email_enabled() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def _locale(value: str | None) -> str:
    return value if value in SUPPORTED_LOCALES else DEFAULT_LOCALE


def _engine():
    from ingest_router import _engine as engine  # lazy - circular import

    return engine()


# ── Links (no document content, only where to look) ──────────────────────────


def link_for(
    kind: str, payload: dict, table_name: str | None, document_id: str | None
) -> str:
    """Path inside the web app a notification points to."""
    if kind in ("sync_down", "backup_failed"):
        return "/ops/sync"
    if kind == "review_pending" and not document_id:
        return "/review"
    if kind == "destruction_proposed":
        return "/lifecycle/destructions"
    if kind == "retention_eligible":
        return "/lifecycle/destructions"
    if table_name and document_id:
        return f"/documents/{table_name}/{document_id}"
    return "/notifications"


def absolute(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path


# ── notify ────────────────────────────────────────────────────────────────────


async def notify(
    conn,
    tenant_id: str,
    user_ids,
    kind: str,
    payload: dict | None = None,
    *,
    table_name: str | None = None,
    document_id: str | None = None,
    coalesce: bool = False,
    exclude: str | None = None,
) -> list[str]:
    """Notify `user_ids` (deduplicated, `exclude` - usually the actor -
    removed). With coalesce=True an unread notification of the same kind
    (and document) is bumped instead of adding another row - its payload
    `count` grows - and no second e-mail goes out for it. Returns the ids
    of the rows created or bumped. Never raises."""
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind!r}")
    recipients = [
        u for u in dict.fromkeys(str(u) for u in user_ids if u) if u != exclude
    ]
    if not recipients:
        return []
    try:
        async with conn.begin_nested():
            return await _notify(
                conn,
                tenant_id,
                recipients,
                kind,
                payload or {},
                table_name,
                document_id,
                coalesce,
            )
    except Exception:
        logger.exception("notify(%s) failed", kind)
        return []


async def _notify(
    conn, tenant_id, recipients, kind, payload, table_name, document_id, coalesce
):
    ids = []
    users = {
        r["id"]: r
        for r in (
            await conn.execute(
                text("""
                    SELECT id::text AS id, email, locale, email_notifications
                    FROM sdai_users
                    WHERE id = ANY(CAST(:ids AS uuid[])) AND tenant_id = CAST(:tid AS uuid)
                      AND is_active IS NOT FALSE
                """),
                {"ids": recipients, "tid": tenant_id},
            )
        ).mappings()
    }
    for user_id in recipients:
        user = users.get(user_id)
        if user is None:
            continue
        if coalesce:
            bumped = (
                await conn.execute(
                    text("""
                        UPDATE sdai_notifications
                        SET payload = jsonb_set(
                                COALESCE(payload, '{}'::jsonb), '{count}',
                                to_jsonb(COALESCE((payload->>'count')::int, 1) + 1)),
                            created_at = now()
                        WHERE id = (
                            SELECT id FROM sdai_notifications
                            WHERE user_id = CAST(:u AS uuid) AND kind = :k AND read_at IS NULL
                              AND table_name IS NOT DISTINCT FROM :t
                              AND document_id IS NOT DISTINCT FROM CAST(:d AS uuid)
                            ORDER BY created_at DESC LIMIT 1
                        )
                        RETURNING id::text
                    """),
                    {"u": user_id, "k": kind, "t": table_name, "d": document_id},
                )
            ).scalar()
            if bumped:
                ids.append(bumped)
                continue
        notification_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_notifications
                        (tenant_id, user_id, kind, payload, table_name, document_id)
                    VALUES (CAST(:tid AS uuid), CAST(:u AS uuid), :k, CAST(:p AS jsonb),
                            :t, CAST(:d AS uuid))
                    RETURNING id::text
                """),
                {
                    "tid": tenant_id,
                    "u": user_id,
                    "k": kind,
                    "p": json.dumps(
                        {"count": 1, **payload} if coalesce else payload, default=str
                    ),
                    "t": table_name,
                    "d": document_id,
                },
            )
        ).scalar()
        ids.append(notification_id)
        if (user["email_notifications"] or "immediate") == "immediate" and user[
            "email"
        ]:
            await enqueue_email(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                to_email=user["email"],
                locale=_locale(user["locale"]),
                kind=kind,
                dedupe_key=f"n:{notification_id}",
                link=link_for(kind, payload, table_name, document_id),
            )
    return ids


async def notify_in_new_transaction(
    tenant_id: str, user_ids, kind: str, payload=None, **kw
) -> None:
    """notify() for callers outside a transaction (after their commit)."""
    try:
        async with _engine().begin() as conn:
            await notify(conn, tenant_id, user_ids, kind, payload, **kw)
    except Exception:
        logger.exception("notify(%s) failed", kind)


# ── Recipients ────────────────────────────────────────────────────────────────


async def users_with_permission(
    conn,
    tenant_id: str,
    table: str,
    doc_id: str,
    permission: str = "read",
    roles=("admin", "reviewer", "reader"),
    user_ids=None,
) -> list[str]:
    """Active people of the tenant (optionally only `user_ids`) holding
    `permission` on the document right now - never notify someone about a
    document they can't open."""
    import documents_router  # lazy - it imports ingest_router
    from auth import load_user

    params = {"tid": tenant_id, "roles": list(roles)}
    extra = ""
    if user_ids is not None:
        if not user_ids:
            return []
        extra = " AND id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = [str(u) for u in user_ids]
    candidates = (
        (
            await conn.execute(
                text(
                    "SELECT id::text FROM sdai_users WHERE tenant_id = CAST(:tid AS uuid)"
                    " AND is_active IS NOT FALSE AND role = ANY(:roles)" + extra
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    allowed = []
    for user_id in candidates:
        user = await load_user(conn, user_id)
        if user and await documents_router._document_visible(
            conn, table, doc_id, user, permission
        ):
            allowed.append(user_id)
    return allowed


async def admins(conn, tenant_id: str | None = None) -> list[tuple[str, str]]:
    """(tenant_id, user_id) of every active admin (of one tenant, or all)."""
    rows = await conn.execute(
        text(
            "SELECT tenant_id::text, id::text FROM sdai_users"
            " WHERE role = 'admin' AND is_active IS NOT FALSE"
            + (" AND tenant_id = CAST(:tid AS uuid)" if tenant_id else "")
        ),
        {"tid": tenant_id} if tenant_id else {},
    )
    return [(r[0], r[1]) for r in rows]


async def notify_review_pending(tenant_id: str, table: str, doc_id: str) -> None:
    """A document landed in the review queue: tell the people who can
    review it. Coalesced into one unread "N documents to review" per person."""
    try:
        async with _engine().begin() as conn:
            reviewers = await users_with_permission(
                conn, tenant_id, table, doc_id, "edit", roles=("admin", "reviewer")
            )
            await notify(
                conn, tenant_id, reviewers, "review_pending", {}, coalesce=True
            )
    except Exception:
        logger.exception("review notification failed")


async def notify_document_shared(
    conn,
    current_user,
    table: str,
    doc_id: str,
    grantee_type: str,
    grantee_id: str,
    permission: str,
) -> None:
    """ "Shared with you" after a document grant - to the person, or every
    member of the group, who can now actually open the document."""
    try:
        async with conn.begin_nested():  # a failure here never aborts the grant
            await _notify_document_shared(
                conn, current_user, table, doc_id, grantee_type, grantee_id, permission
            )
    except Exception:
        logger.exception("share notification failed")


async def _notify_document_shared(
    conn, current_user, table, doc_id, grantee_type, grantee_id, permission
):
    if grantee_type == "user":
        grantees = [grantee_id]
    else:
        grantees = (
            (
                await conn.execute(
                    text(
                        "SELECT user_id::text FROM sdai_user_groups WHERE group_id = CAST(:g AS uuid)"
                    ),
                    {"g": grantee_id},
                )
            )
            .scalars()
            .all()
        )
    record_id = (
        await conn.execute(
            text(f'SELECT record_id FROM "{table}" WHERE id = CAST(:id AS uuid)'),
            {"id": doc_id},
        )
    ).scalar()
    await notify(
        conn,
        current_user.tenant_id,
        await users_with_permission(
            conn, current_user.tenant_id, table, doc_id, "read", user_ids=list(grantees)
        ),
        "document_shared",
        {
            "record_id": record_id,
            "by": current_user.full_name or current_user.email,
            "permission": permission,
        },
        table_name=table,
        document_id=doc_id,
        exclude=current_user.id,
    )


# ── E-mail outbox ─────────────────────────────────────────────────────────────


def render_email(
    kind: str, locale: str, link: str, count: int | None = None
) -> tuple[str, str]:
    """(subject, body) - a fixed sentence per kind, never document content."""
    subject = t(f"email.subject.{kind}", locale)
    lines = [
        t("email.greeting", locale),
        "",
        t(f"email.body.{kind}", locale, count=count or 1),
        "",
        t("email.open", locale, url=absolute(link)),
        "",
        t("email.footer", locale, url=absolute("/notifications")),
    ]
    return f"[SDAI] {subject}", "\n".join(lines)


async def enqueue_email(
    conn,
    *,
    tenant_id: str | None,
    user_id: str | None,
    to_email: str,
    locale: str,
    kind: str,
    dedupe_key: str,
    link: str,
    subject: str | None = None,
    body: str | None = None,
) -> bool:
    """Queue one e-mail - idempotent on dedupe_key. Nothing is queued while
    SMTP isn't configured on this node. True when a row was added."""
    if not email_enabled():
        return False
    if subject is None or body is None:
        subject, body = render_email(kind, locale, link)
    added = await conn.execute(
        text("""
            INSERT INTO sdai_email_outbox
                (tenant_id, user_id, to_email, locale, kind, dedupe_key, subject, body)
            VALUES (CAST(:tid AS uuid), CAST(:u AS uuid), :to, :l, :k, :dk, :s, :b)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING id
        """),
        {
            "tid": tenant_id,
            "u": user_id,
            "to": to_email,
            "l": locale,
            "k": kind,
            "dk": dedupe_key,
            "s": subject,
            "b": body,
        },
    )
    return added.scalar() is not None


def retry_delay(attempts: int) -> timedelta:
    """Backoff after the n-th failed attempt: 1, 2, 4 ... minutes, max 6 h."""
    return timedelta(minutes=min(2 ** max(attempts - 1, 0), 360))


def _send_smtp(row: dict) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = row["to_email"]
    msg["Subject"] = row["subject"]
    msg["Date"] = formatdate(localtime=True)
    domain = SMTP_FROM.rsplit("@", 1)[-1].strip(">") or "sdai.local"
    msg["Message-ID"] = f"<{row['id']}@{domain}>"
    msg["Content-Language"] = row["locale"]
    msg.set_content(row["body"])
    if SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=30, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    with server:
        if SMTP_SECURITY == "starttls":
            server.starttls(context=ssl.create_default_context())
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


async def send_due(engine, sender=None, limit: int = 20) -> dict:
    """Send the outbox rows that are due. Rows are claimed ('sending') with
    SKIP LOCKED, so two workers never pick the same row. Returns counts."""
    sender = sender or _send_smtp
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sdai_email_outbox SET status = 'pending'"
                " WHERE status = 'sending' AND claimed_at < :stale"
            ),
            {"stale": datetime.now(timezone.utc) - _STALE_SENDING},
        )
        # Accounts deactivated or deleted since (here or on the peer) get
        # nothing more.
        await conn.execute(
            text(
                "UPDATE sdai_email_outbox SET status = 'failed', last_error = 'account deactivated'"
                " WHERE status = 'pending' AND user_id IN (SELECT id FROM sdai_users"
                " WHERE is_active IS FALSE OR deleted_at IS NOT NULL)"
            )
        )
        rows = (
            (
                await conn.execute(
                    text("""
                    UPDATE sdai_email_outbox SET status = 'sending', attempts = attempts + 1,
                           claimed_at = now()
                    WHERE id IN (
                        SELECT id FROM sdai_email_outbox
                        WHERE status = 'pending' AND next_attempt_at <= now()
                        ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id::text AS id, to_email, subject, body, locale, attempts
                """),
                    {"n": limit},
                )
            )
            .mappings()
            .all()
        )
    counts = {"sent": 0, "retry": 0, "failed": 0}
    for row in rows:
        try:
            await asyncio.to_thread(sender, dict(row))
        except Exception as exc:
            final = row["attempts"] >= EMAIL_MAX_ATTEMPTS
            async with engine.begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE sdai_email_outbox
                        SET status = :st, last_error = :err, next_attempt_at = :next
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {
                        "st": "failed" if final else "pending",
                        "err": f"{type(exc).__name__}: {exc}"[:500],
                        "next": datetime.now(timezone.utc)
                        + retry_delay(row["attempts"]),
                        "id": row["id"],
                    },
                )
            counts["failed" if final else "retry"] += 1
            continue
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE sdai_email_outbox SET status = 'sent', sent_at = now(), last_error = NULL"
                    " WHERE id = CAST(:id AS uuid)"
                ),
                {"id": row["id"]},
            )
        counts["sent"] += 1
    return counts


async def email_worker() -> None:
    while True:
        await asyncio.sleep(EMAIL_POLL_SECONDS)
        if not email_enabled():
            continue
        try:
            await send_due(_engine())
        except Exception:
            logger.exception("email outbox run failed")


# ── Daily digest (local node) ────────────────────────────────────────────────


async def build_digests(engine, today) -> int:
    """Queue one digest per 'digest' user with unread notifications from
    the last 24 h. Idempotent per day (dedupe_key digest:<user>:<date>)."""
    if not email_enabled():
        return 0
    queued = 0
    async with engine.begin() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT u.id::text AS user_id, u.tenant_id::text AS tenant_id, u.email, u.locale,
                           n.kind, count(*) AS n
                    FROM sdai_users u
                    JOIN sdai_notifications n ON n.user_id = u.id
                    WHERE u.email_notifications = 'digest' AND u.is_active IS NOT FALSE
                      AND u.email IS NOT NULL
                      AND n.read_at IS NULL AND n.created_at > now() - interval '24 hours'
                    GROUP BY u.id, u.tenant_id, u.email, u.locale, n.kind
                    ORDER BY u.id, n.kind
                """)
                )
            )
            .mappings()
            .all()
        )
        by_user: dict[str, dict] = {}
        for r in rows:
            entry = by_user.setdefault(
                r["user_id"],
                {
                    "tenant_id": r["tenant_id"],
                    "email": r["email"],
                    "locale": _locale(r["locale"]),
                    "kinds": [],
                },
            )
            entry["kinds"].append((r["kind"], r["n"]))
        for user_id, entry in by_user.items():
            locale = entry["locale"]
            lines = [
                t("email.greeting", locale),
                "",
                t("email.digest_intro", locale),
                "",
            ]
            for kind, n in entry["kinds"]:
                lines.append(f"  - {t(f'email.subject.{kind}', locale)} ({n})")
            lines += [
                "",
                t("email.open", locale, url=absolute("/notifications")),
                "",
                t("email.footer", locale, url=absolute("/notifications")),
            ]
            if await enqueue_email(
                conn,
                tenant_id=entry["tenant_id"],
                user_id=user_id,
                to_email=entry["email"],
                locale=locale,
                kind="digest",
                dedupe_key=f"digest:{user_id}:{today.isoformat()}",
                link="/notifications",
                subject=f"[SDAI] {t('email.subject.digest', locale)}",
                body="\n".join(lines),
            ):
                queued += 1
    return queued


async def digest_worker() -> None:
    while True:
        await asyncio.sleep(600)
        now = datetime.now()
        if now.hour < EMAIL_DIGEST_HOUR or not email_enabled():
            continue
        try:
            await build_digests(_engine(), now.date())
        except Exception:
            logger.exception("digest run failed")


# ── Ops alerts (every node) ──────────────────────────────────────────────────


async def check_ops_alerts(engine) -> list[str]:
    """Notify every admin once when sync_down / backup_failed becomes
    active on this node; forget it when it clears. Returns the kinds
    newly notified."""
    import ingest_router
    from sync.admin import compute_alerts

    async with engine.begin() as conn:
        active = {
            a["kind"]: a
            for a in await compute_alerts(conn)
            if a["kind"] in OPS_ALERT_KINDS
        }
        known = set(
            (await conn.execute(text("SELECT kind FROM sdai_ops_alerts")))
            .scalars()
            .all()
        )
        for kind in known - set(active):
            await conn.execute(
                text("DELETE FROM sdai_ops_alerts WHERE kind = :k"), {"k": kind}
            )
        raised = []
        for kind in sorted(set(active) - known):
            await conn.execute(
                text(
                    "INSERT INTO sdai_ops_alerts (kind) VALUES (:k) ON CONFLICT DO NOTHING"
                ),
                {"k": kind},
            )
            payload = {
                "node_id": ingest_router.NODE_ID,
                "since": active[kind].get("since"),
            }
            by_tenant: dict[str, list[str]] = {}
            for tenant_id, user_id in await admins(conn):
                by_tenant.setdefault(tenant_id, []).append(user_id)
            for tenant_id, users in by_tenant.items():
                await notify(conn, tenant_id, users, kind, payload)
            raised.append(kind)
    return raised


async def ops_alert_worker() -> None:
    while True:
        await asyncio.sleep(OPS_ALERT_CHECK_SECONDS)
        try:
            await check_ops_alerts(_engine())
        except Exception:
            logger.exception("ops alert check failed")
