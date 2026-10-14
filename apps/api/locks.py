"""Check-out / check-in (GED Phase 7.2) - the document lock.

A document is checked out when locked_by is set and locked_until is NULL or
in the future (an expired lock is already free in every check; the sweep
only clears the columns). While it is checked out, only the holder may
change it: unlocked_sql is folded into the `edit` level of
documents_router._access_where_clause - for admins too - so every write
path (fields, versions, review, tags, series, links, page actions, trash)
refuses at once, with a 423 "checked out by X" (locked_error).

  checkout   edit permission (so never on an archived document unless the
             caller manages the lifecycle), default CHECKOUT_DEFAULT_HOURS;
             the holder may check out again to extend.
  checkin    the holder - optionally with a new version (versions_router).
  unlock     an admin forces it open; the holder is notified.
  expiry     expire_locks clears expired locks (local node, sweep).

Across nodes the lock is an ordinary synced column: while the nodes can't
reach each other, two people can check out the same document on each
side - the lock is advisory then, and concurrent edits are resolved by the
sync conflict rules (Phase 8).

No router imports at module level (documents_router imports this module).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

CHECKOUT_DEFAULT_HOURS = int(os.getenv("CHECKOUT_DEFAULT_HOURS", "8"))
CHECKOUT_MAX_HOURS = int(os.getenv("CHECKOUT_MAX_HOURS", "72"))
LOCK_SWEEP_SECONDS = int(os.getenv("LOCK_SWEEP_SECONDS", "300"))

_TABLE_RE = re.compile(r"^[a-z0-9_]+$")


def _check(table: str, user_id: str) -> tuple[str, str]:
    if not _TABLE_RE.fullmatch(table):
        raise ValueError(f"bad table name {table!r}")
    return table, str(uuid.UUID(str(user_id)))  # raises on anything but a uuid


def held_by_other_sql(table: str, user_id: str) -> str:
    """SQL condition: the row is checked out by someone other than user_id.
    Both values are validated literals (a table name and a uuid)."""
    table, user_id = _check(table, user_id)
    doc = f'"{table}"'
    return (
        f"({doc}.locked_by IS NOT NULL AND {doc}.locked_by <> CAST('{user_id}' AS uuid)"
        f" AND ({doc}.locked_until IS NULL OR {doc}.locked_until > now()))"
    )


def unlocked_sql(table: str, user_id: str) -> str:
    """SQL condition: user_id may change the row as far as the lock goes."""
    return f"NOT {held_by_other_sql(table, user_id)}"


def is_active(locked_by, locked_until, now: datetime | None = None) -> bool:
    if not locked_by:
        return False
    now = now or datetime.now(timezone.utc)
    return locked_until is None or locked_until > now


async def state(
    conn, table: str, doc_id: str, *, for_update: bool = False
) -> dict | None:
    """The document's lock: locked_by, locked_until, holder name/email,
    active - or None when the row doesn't exist."""
    _check(table, uuid.UUID(int=0))
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT d.locked_by::text AS locked_by, d.locked_until,"
                    f" u.full_name AS holder_name, u.email AS holder_email"
                    f' FROM "{table}" d LEFT JOIN sdai_users u ON u.id = d.locked_by'
                    f" WHERE d.id = CAST(:id AS uuid)"
                    + (" FOR UPDATE OF d" if for_update else "")
                ),
                {"id": doc_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    out = dict(row)
    out["active"] = is_active(out["locked_by"], out["locked_until"])
    return out


def holder_label(lock: dict) -> str:
    return lock.get("holder_name") or lock.get("holder_email") or "?"


async def locked_error(conn, table: str, doc_id: str, locale: str):
    """The HTTPException for an edit refused because someone else holds
    the lock: 423 'Checked out by X until ...'."""
    from fastapi import HTTPException
    from i18n import format_datetime, t

    lock = await state(conn, table, doc_id) or {}
    until = lock.get("locked_until")
    return HTTPException(
        status_code=423,
        detail=t(
            "locks.locked_by",
            locale,
            name=holder_label(lock),
            until=format_datetime(until, locale),
        ),
    )


class LockError(Exception):
    def __init__(self, status: int, key: str, **params):
        super().__init__(key)
        self.status, self.key, self.params = status, key, params


async def checkout(conn, table: str, doc_id: str, user_id: str, hours: int) -> dict:
    """Lock the document for user_id for `hours`. The caller has already
    checked the edit permission (which refuses a lock held by someone
    else); this re-checks under a row lock against a concurrent checkout."""
    lock = await state(conn, table, doc_id, for_update=True)
    if lock is None:
        raise LockError(404, "common.document_not_found")
    if lock["active"] and lock["locked_by"] != user_id:
        raise LockError(423, "locks.locked_by", name=holder_label(lock))
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    await conn.execute(
        text(
            f'UPDATE "{table}" SET locked_by = CAST(:u AS uuid), locked_until = :until'
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"u": user_id, "until": until, "id": doc_id},
    )
    return {"locked_by": user_id, "locked_until": until, "extended": lock["active"]}


async def release(conn, table: str, doc_id: str, *, holder: str | None = None) -> dict:
    """Clear the lock. With holder=..., only that person's active lock
    (409 otherwise). Returns the lock as it was."""
    lock = await state(conn, table, doc_id, for_update=True)
    if lock is None:
        raise LockError(404, "common.document_not_found")
    if holder is not None and (not lock["active"] or lock["locked_by"] != holder):
        raise LockError(409, "locks.not_holder")
    if holder is None and not lock["active"]:
        raise LockError(409, "locks.not_locked")
    await conn.execute(
        text(
            f'UPDATE "{table}" SET locked_by = NULL, locked_until = NULL'
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": doc_id},
    )
    return lock


async def expire_locks(engine) -> int:
    """Clear every expired lock (all per-type tables), audit each as an
    automatic check-in. Returns how many were released."""
    from audit import log_action

    async with engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text("""
                    SELECT table_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND column_name = 'locked_until'
                      AND table_name LIKE 't\\_%'
                """)
                )
            )
            .scalars()
            .all()
        )
    released = []
    for table in tables:
        if not _TABLE_RE.fullmatch(table):
            continue
        async with engine.begin() as conn:
            rows = await conn.execute(
                text(f"""
                    UPDATE "{table}" SET locked_by = NULL, locked_until = NULL
                    WHERE locked_by IS NOT NULL AND locked_until <= now()
                    RETURNING id::text, record_id
                """)
            )
            released += [(table, r[0], r[1]) for r in rows]
    for table, doc_id, record_id in released:
        await log_action(
            action="document_checked_in",
            table_name=table,
            document_id=doc_id,
            details={"reason": "expired", "record_id": record_id},
        )
    return len(released)


async def expiry_worker() -> None:
    from ingest_router import _engine

    while True:
        await asyncio.sleep(LOCK_SWEEP_SECONDS)
        try:
            await expire_locks(_engine())
        except Exception:
            logger.exception("lock expiry sweep failed")
