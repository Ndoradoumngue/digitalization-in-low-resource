"""
The caller's notifications and e-mail preferences (GED Phase 7.1).

Endpoints (any signed-in person, own rows only):
  GET  /api/db/notifications[?unread_only=&before=&limit=]  newest first
  GET  /api/db/notifications/unread-count                   the bell's badge
  POST /api/db/notifications/{id}/read                      mark one read
  POST /api/db/notifications/read-all                       mark all read
  GET  /api/db/notification-preferences                     e-mail mode + language
  PUT  /api/db/notification-preferences                     change them

Admin - this server's e-mail outbox (node-local):
  GET  /api/admin/emails[?status=problems|pending|sending|sent|failed]
  POST /api/admin/emails/retry      {ids?} failed → pending again (all when no ids)
  POST /api/admin/emails/discard    {ids?} drop failed e-mails

A notification about a document the person can no longer read (grant
removed, document trashed or destroyed since) is still listed, but without
the document reference or its payload details - access is re-checked on
every read, never trusted from the moment the notification was created.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Literal

import notifications
from audit import client_ip, log_action
from auth import CurrentUser, require_admin, require_user
from documents_router import _access_params, _access_where_clause, _get_tables_columns
from fastapi import APIRouter, Depends, Query, Request
from ingest_router import _engine
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(prefix="/api/db", tags=["notifications"])

# Payload keys that never describe a document - kept when the document
# itself is no longer readable.
_NEUTRAL_KEYS = frozenset({"count", "node_id", "since"})


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _readable(
    conn, current_user: CurrentUser, refs: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """The (table, id) pairs among `refs` the caller can read right now."""
    if not refs:
        return set()
    tables = await _get_tables_columns(conn, current_user.tenant_slug)
    readable = set()
    by_table: dict[str, list[str]] = {}
    for table, doc_id in refs:
        if table in tables:
            by_table.setdefault(table, []).append(doc_id)
    for table, ids in by_table.items():
        clause = _access_where_clause(table, current_user)
        rows = await conn.execute(
            text(
                f'SELECT id::text FROM "{table}" WHERE id = ANY(CAST(:ids AS uuid[]))'
                + (f" AND {clause}" if clause else "")
            ),
            {"ids": ids, **_access_params(current_user)},
        )
        readable.update((table, r[0]) for r in rows)
    return readable


def _shape(row: dict, readable: set[tuple[str, str]]) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = payload or {}
    table, doc_id = row["table_name"], row["document_id"]
    if doc_id and (table, doc_id) not in readable:
        table = doc_id = None
        payload = {k: v for k, v in payload.items() if k in _NEUTRAL_KEYS}
        payload["unavailable"] = True
    return {
        "id": row["id"],
        "kind": row["kind"],
        "table_name": table,
        "document_id": doc_id,
        "payload": payload,
        "link": notifications.link_for(row["kind"], payload, table, doc_id)
        if not payload.get("unavailable")
        else "/notifications",
        "created_at": _iso(row["created_at"]),
        "read_at": _iso(row["read_at"]),
    }


@router.get("/notifications", summary="My notifications")
async def list_notifications(
    unread_only: bool = False,
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_user),
):
    where = ["user_id = CAST(:u AS uuid)", "tenant_id = CAST(:tid AS uuid)"]
    params = {"u": current_user.id, "tid": current_user.tenant_id, "n": limit}
    if unread_only:
        where.append("read_at IS NULL")
    if before is not None:
        where.append("created_at < :before")
        params["before"] = before
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT id::text AS id, kind, payload, table_name,
                           document_id::text AS document_id, created_at, read_at
                    FROM sdai_notifications
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at DESC LIMIT :n
                """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        readable = await _readable(
            conn,
            current_user,
            {(r["table_name"], r["document_id"]) for r in rows if r["document_id"]},
        )
    return [_shape(dict(r), readable) for r in rows]


@router.get("/notifications/unread-count", summary="Number of unread notifications")
async def unread_count(current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM sdai_notifications WHERE user_id = CAST(:u AS uuid)"
                    " AND tenant_id = CAST(:tid AS uuid) AND read_at IS NULL"
                ),
                {"u": current_user.id, "tid": current_user.tenant_id},
            )
        ).scalar()
    return {"count": count or 0}


@router.post("/notifications/read-all", summary="Mark all my notifications read")
async def read_all(current_user: CurrentUser = Depends(require_user)):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE sdai_notifications SET read_at = now()"
                " WHERE user_id = CAST(:u AS uuid) AND read_at IS NULL"
            ),
            {"u": current_user.id},
        )
    return {"ok": True, "updated": result.rowcount}


@router.post(
    "/notifications/{notification_id}/read", summary="Mark a notification read"
)
async def read_notification(
    notification_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().begin() as conn:
        await conn.execute(
            text("""
                UPDATE sdai_notifications SET read_at = now()
                WHERE id = CAST(:id AS uuid) AND user_id = CAST(:u AS uuid) AND read_at IS NULL
            """),
            {"id": _uuid_or_zero(notification_id), "u": current_user.id},
        )
    return {"ok": True}


def _uuid_or_zero(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return "00000000-0000-0000-0000-000000000000"


class PreferencesBody(BaseModel):
    email_notifications: Literal["immediate", "digest", "off"] | None = None
    locale: Literal["fr", "en"] | None = None


async def _preferences(conn, current_user: CurrentUser) -> dict:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT email_notifications, locale FROM sdai_users WHERE id = CAST(:u AS uuid)"
                ),
                {"u": current_user.id},
            )
        )
        .mappings()
        .one()
    )
    return {
        "email_notifications": row["email_notifications"] or "immediate",
        "locale": row["locale"],
        "email_enabled": notifications.email_enabled(),
    }


@router.get("/notification-preferences", summary="My notification preferences")
async def get_preferences(current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        return await _preferences(conn, current_user)


@router.put("/notification-preferences", summary="Change my notification preferences")
async def put_preferences(
    body: PreferencesBody,
    request: Request,
    current_user: CurrentUser = Depends(require_user),
):
    changes = body.model_dump(exclude_none=True)
    async with _engine().begin() as conn:
        if changes:
            sets = ", ".join(f"{col} = :{col}" for col in changes)
            await conn.execute(
                text(f"UPDATE sdai_users SET {sets} WHERE id = CAST(:u AS uuid)"),
                {**changes, "u": current_user.id},
            )
        prefs = await _preferences(conn, current_user)
    if changes:
        await log_action(
            action="notification_preferences_changed",
            user_id=current_user.id,
            user_email=current_user.email,
            details=changes,
            ip_address=client_ip(request),
        )
    return prefs


# ── Admin: this node's e-mail outbox ──────────────────────────────────────────
# The outbox is node-local: each server shows (and retries) the e-mails it
# queued itself. An admin sees the rows of their tenant (and tenant-less ones).

admin_router = APIRouter(prefix="/api/admin/emails", tags=["notifications-admin"])

_OUTBOX_SCOPE = "(tenant_id = CAST(:tid AS uuid) OR tenant_id IS NULL)"
_OUTBOX_STATUSES = ("pending", "sending", "sent", "failed")


async def failed_email_count(conn, tenant_id: str) -> int:
    return (
        await conn.execute(
            text(
                f"SELECT count(*) FROM sdai_email_outbox WHERE status = 'failed' AND {_OUTBOX_SCOPE}"
            ),
            {"tid": tenant_id},
        )
    ).scalar() or 0


@admin_router.get("", summary="E-mails queued on this server")
async def list_emails(
    status: Literal["problems", "pending", "sending", "sent", "failed"] = "problems",
    current_user: CurrentUser = Depends(require_admin),
):
    """`problems` (default) = failed e-mails and pending ones that already
    failed at least once (retrying)."""
    where = (
        "(status = 'failed' OR (status = 'pending' AND attempts > 0))"
        if status == "problems"
        else "status = :st"
    )
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT id::text AS id, kind, to_email, subject, status, attempts, last_error,
                           created_at, next_attempt_at, sent_at
                    FROM sdai_email_outbox
                    WHERE {where} AND {_OUTBOX_SCOPE}
                    ORDER BY created_at DESC LIMIT 200
                """),
                    {"st": status, "tid": current_user.tenant_id},
                )
            )
            .mappings()
            .all()
        )
        counts = {
            r[0]: r[1]
            for r in await conn.execute(
                text(
                    f"SELECT status, count(*) FROM sdai_email_outbox WHERE {_OUTBOX_SCOPE}"
                    " GROUP BY status"
                ),
                {"tid": current_user.tenant_id},
            )
        }
    return {
        "email_enabled": notifications.email_enabled(),
        "counts": {s: counts.get(s, 0) for s in _OUTBOX_STATUSES},
        "items": [{k: _iso(v) for k, v in dict(r).items()} for r in rows],
    }


class EmailIdsBody(BaseModel):
    ids: list[str] | None = None  # None = every failed e-mail


def _ids_filter(body: EmailIdsBody | None) -> tuple[str, dict]:
    if body is None or body.ids is None:
        return "", {}
    ids = [i for i in (_uuid_or_none(x) for x in body.ids[:500]) if i]
    return " AND id = ANY(CAST(:ids AS uuid[]))", {"ids": ids}


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


@admin_router.post("/retry", summary="Send failed e-mails again")
async def retry_emails(
    request: Request,
    body: EmailIdsBody | None = None,
    current_user: CurrentUser = Depends(require_admin),
):
    extra, params = _ids_filter(body)
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(f"""
                UPDATE sdai_email_outbox
                SET status = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL
                WHERE status = 'failed' AND {_OUTBOX_SCOPE}{extra}
            """),
            {"tid": current_user.tenant_id, **params},
        )
    await log_action(
        action="emails_retried",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"count": result.rowcount},
        ip_address=client_ip(request),
    )
    return {"retried": result.rowcount}


@admin_router.post("/discard", summary="Drop failed e-mails")
async def discard_emails(
    request: Request,
    body: EmailIdsBody | None = None,
    current_user: CurrentUser = Depends(require_admin),
):
    extra, params = _ids_filter(body)
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(
                f"DELETE FROM sdai_email_outbox WHERE status = 'failed' AND {_OUTBOX_SCOPE}{extra}"
            ),
            {"tid": current_user.tenant_id, **params},
        )
    await log_action(
        action="emails_discarded",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"count": result.rowcount},
        ip_address=client_ip(request),
    )
    return {"discarded": result.rowcount}
