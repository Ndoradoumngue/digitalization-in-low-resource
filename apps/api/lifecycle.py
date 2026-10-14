"""Document lifecycle (GED Phase 6) - the ONLY code that changes a
document's lifecycle_status, its trash columns (deleted_at / deleted_by /
deleted_reason) or archived_at. Routes, the retention job and the
destruction path all call in here, so every rule is enforced in one place:

    active ──archive──▶ archived ──retention job──▶ destruction_eligible
                           ▲                               │
                           └──────── extend (archivist) ───┤
                                                           ▼
                                       destruction (approved) ──▶ destroyed

  - archive: active → archived, by a lifecycle manager (can_manage_lifecycle).
  - retention: archived → destruction_eligible, by the daily retention job
    only (retention.run_daily), once retention_until has passed.
  - extend: destruction_eligible → archived with a new retention date
    (retention_override), by a lifecycle manager - the "review" outcome.
  - destruction: destruction_eligible → destroyed, only when executing an
    approved destruction record (destruction.py).
  - purge: a document in the trash for TRASH_RETENTION_DAYS → destroyed,
    whatever its status, through the same destruction path with an
    automatic approval record ("purge corbeille").

The trash is orthogonal to the status: trashing sets deleted_at (the row
disappears from every user-facing query, see
documents_router._live_where_clause) and restoring clears it.

A legal hold (held_sql: an active hold naming
it or a series above it) blocks trash, purge, destruction and the move to
destruction_eligible. Holds are checked here, in SQL, at the moment of the
change - never from a value cached in Python.

Archived and destruction-eligible documents are read-only for everyone
but lifecycle managers: documents_router._access_where_clause folds
editable_sql into the `edit` level, so every edit-level action (fields,
review, versions, tags, series, links, page actions) is refused at once.

No router imports at module level (ingest_router / documents_router import
this module).
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

STATUSES = ("active", "archived", "destruction_eligible", "destroyed")

TRASH_RETENTION_DAYS = int(os.getenv("TRASH_RETENTION_DAYS", "30"))

# (from, to) -> the one operation allowed to make that move. Anything not
# listed is refused (LifecycleError 409).
TRANSITIONS: dict[tuple[str, str], str] = {
    ("active", "archived"): "archive",
    ("archived", "destruction_eligible"): "retention",
    ("destruction_eligible", "archived"): "extend",
    ("destruction_eligible", "destroyed"): "destruction",
}
# A trashed document is purged (destroyed) from any status but destroyed.
PURGE_FROM = ("active", "archived", "destruction_eligible")

# Statuses whose documents only lifecycle managers may change.
READ_ONLY_STATUSES = ("archived", "destruction_eligible")

# Columns a tombstone keeps besides the GED/system ones: what identifies
# the record and where it was filed. Every other extracted field is nulled.
_TOMBSTONE_KEEP = frozenset({"record_id", "series_id", "uploaded_by", "document_type"})
# File references of the row itself - the files are deleted, so are these.
_TOMBSTONE_FILE_COLS = (
    "source_image_path",
    "source_pdf_path",
    "page_image_paths",
    "original_path",
)

_TABLE_RE = re.compile(r"^[a-z0-9_]+$")


class LifecycleError(Exception):
    """A refused lifecycle change - `key` is an i18n message key (the
    router translates it), `status` the HTTP status to answer with."""

    def __init__(self, status: int, key: str, **params):
        super().__init__(key)
        self.status = status
        self.key = key
        self.params = params


def _check_table(table: str) -> str:
    if not _TABLE_RE.fullmatch(table):
        raise ValueError(f"bad table name {table!r}")
    return table


def transition_op(frm: str | None, to: str) -> str | None:
    """The operation allowed to move a document from `frm` to `to`, or None."""
    return TRANSITIONS.get((frm or "active", to))


def check_transition(frm: str | None, to: str, op: str) -> None:
    """Raise unless `op` is the operation allowed to move `frm` → `to`."""
    frm = frm or "active"
    allowed = TRANSITIONS.get((frm, to))
    if op == "purge" and to == "destroyed" and frm in PURGE_FROM:
        return
    if allowed != op:
        raise LifecycleError(409, "lifecycle.illegal_transition", frm=frm, to=to)


def can_edit_status(status: str | None, can_manage_lifecycle: bool) -> bool:
    """Whether someone may change a document in this lifecycle status
    (fields, versions, review, filing...). Destroyed rows are never edited."""
    status = status or "active"
    if status == "destroyed":
        return False
    return status not in READ_ONLY_STATUSES or can_manage_lifecycle


def editable_sql(table: str) -> str:
    """SQL condition: the row is in a status anyone with `edit` may change
    (folded into the edit level for users who don't manage the lifecycle)."""
    _check_table(table)
    return f"COALESCE(\"{table}\".lifecycle_status, 'active') = 'active'"


def held_sql(table: str, alias: str | None = None) -> str:
    """SQL condition: the row is under legal hold - an active (not released)
    hold names it (scope 'documents') or its series or a series above it
    (scope 'series', always recursive, so a document filed later into a
    held series is held too). The holds are the only source of truth: the
    row's legal_hold column is a copy for lists (refresh_hold_flags).
    `alias` replaces the quoted table name when the outer query aliases it."""
    _check_table(table)
    doc = alias or f'"{table}"'
    return f"""(
        EXISTS (
            SELECT 1 FROM sdai_legal_hold_documents lhd
            JOIN sdai_legal_holds lh ON lh.id = lhd.hold_id
            WHERE lh.released_at IS NULL AND lh.scope = 'documents'
              AND lhd.table_name = '{table}' AND lhd.document_id = {doc}.id
        )
        OR EXISTS (
            SELECT 1 FROM sdai_legal_holds lh
            JOIN sdai_series hs ON hs.id = {doc}.series_id
            WHERE lh.released_at IS NULL AND lh.scope = 'series'
              AND (hs.id = lh.series_id OR strpos(hs.path, '/' || lh.series_id::text || '/') > 0)
        )
    )"""


async def load(conn, table: str, doc_id: str, *, lock: bool = False) -> dict | None:
    """The lifecycle columns of one document (+ `held`), or None. With
    lock=True the row is locked until the transaction ends, so two
    concurrent changes can't both pass the checks."""
    _check_table(table)
    row = (
        (
            await conn.execute(
                text(f"""
                SELECT id::text AS id, COALESCE(lifecycle_status, 'active') AS lifecycle_status,
                       deleted_at, deleted_by::text AS deleted_by, deleted_reason,
                       archived_at, retention_until, retention_override,
                       retention_rule_id::text AS retention_rule_id, content_hash,
                       record_id, series_id::text AS series_id, legal_hold,
                       {held_sql(table)} AS held
                FROM "{table}" WHERE id = CAST(:id AS uuid)
                {"FOR UPDATE" if lock else ""}
            """),
                {"id": doc_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def _require(conn, table: str, doc_id: str) -> dict:
    row = await load(conn, table, doc_id, lock=True)
    if row is None:
        raise LifecycleError(404, "common.document_not_found")
    if row["lifecycle_status"] == "destroyed":
        raise LifecycleError(409, "lifecycle.destroyed")
    return row


def _uuid_or_none(value: str | None) -> str | None:
    return value or None


# ── Archive ───────────────────────────────────────────────────────────────────


async def archive(conn, table: str, doc_id: str, user_id: str | None) -> dict:
    """active → archived. Refused in the trash. Returns the old row."""
    row = await _require(conn, table, doc_id)
    if row["deleted_at"] is not None:
        raise LifecycleError(409, "lifecycle.in_trash")
    check_transition(row["lifecycle_status"], "archived", "archive")
    await conn.execute(
        text(f"""
            UPDATE "{table}" SET lifecycle_status = 'archived', archived_at = now(),
                   archived_by = CAST(:u AS uuid)
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": doc_id, "u": _uuid_or_none(user_id)},
    )
    return row


# ── Trash ─────────────────────────────────────────────────────────────────────


async def trash(
    conn, table: str, doc_id: str, user_id: str | None, reason: str | None = None
) -> dict:
    """Move a document to the trash (soft delete). Its links, grants, tags
    and versions stay - hidden with it - so a restore brings everything
    back. Refused under legal hold. Returns the old row."""
    row = await _require(conn, table, doc_id)
    if row["deleted_at"] is not None:
        raise LifecycleError(409, "lifecycle.already_in_trash")
    if row["held"]:
        raise LifecycleError(409, "lifecycle.legal_hold")
    await conn.execute(
        text(f"""
            UPDATE "{table}" SET deleted_at = now(), deleted_by = CAST(:u AS uuid),
                   deleted_reason = :r
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": doc_id, "u": _uuid_or_none(user_id), "r": (reason or None)},
    )
    return row


def purge_due(deleted_at: datetime | None, now: datetime | None = None) -> bool:
    """Whether a document trashed at `deleted_at` is past the trash
    retention (purged by the daily job, no longer restorable)."""
    if deleted_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return deleted_at + timedelta(days=TRASH_RETENTION_DAYS) <= now


def purge_at(deleted_at: datetime | None) -> datetime | None:
    return deleted_at + timedelta(days=TRASH_RETENTION_DAYS) if deleted_at else None


async def live_duplicate(
    conn,
    tenant_slug: str,
    content_hash: str | None,
    *,
    exclude: tuple[str, str] | None = None,
) -> dict | None:
    """A live (not trashed, not destroyed) document of the tenant holding
    the same file, or None - the dedup check a restore must pass (only one
    live document per file, see ingest_router._find_duplicate)."""
    if not content_hash:
        return None
    tables = (
        (
            await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE column_name = 'content_hash' AND table_schema = 'public'"
                    "   AND starts_with(table_name, :prefix)"
                ),
                {"prefix": f"t_{tenant_slug}_"},
            )
        )
        .scalars()
        .all()
    )
    for table in sorted(tables):
        _check_table(table)
        row = (
            await conn.execute(
                text(f"""
                    SELECT id::text, record_id FROM "{table}"
                    WHERE content_hash = :h AND deleted_at IS NULL
                      AND lifecycle_status IS DISTINCT FROM 'destroyed'
                      AND NOT (id = CAST(:xid AS uuid) AND :xt = '{table}')
                    LIMIT 1
                """),
                {
                    "h": content_hash,
                    "xid": exclude[1]
                    if exclude
                    else "00000000-0000-0000-0000-000000000000",
                    "xt": exclude[0] if exclude else "",
                },
            )
        ).one_or_none()
        if row is not None:
            return {"table_name": table, "id": row[0], "record_id": row[1]}
    return None


async def restore(conn, table: str, doc_id: str, tenant_slug: str) -> dict:
    """Bring a document back from the trash, within TRASH_RETENTION_DAYS.
    Refused (409) when the same file was uploaded again meanwhile - there
    can only be one live document per file. Returns the old row."""
    row = await _require(conn, table, doc_id)
    if row["deleted_at"] is None:
        raise LifecycleError(409, "lifecycle.not_in_trash")
    if purge_due(row["deleted_at"]):
        raise LifecycleError(409, "lifecycle.trash_expired")
    dup = await live_duplicate(
        conn, tenant_slug, row["content_hash"], exclude=(table, doc_id)
    )
    if dup is not None:
        raise LifecycleError(
            409, "lifecycle.restore_duplicate", record=dup["record_id"] or dup["id"]
        )
    await conn.execute(
        text(f"""
            UPDATE "{table}" SET deleted_at = NULL, deleted_by = NULL, deleted_reason = NULL
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": doc_id},
    )
    return row


# ── Retention outcomes ────────────────────────────────────────────────────────


async def mark_eligible(
    conn, table: str, doc_id: str, today: date | None = None
) -> bool:
    """archived → destruction_eligible, for the retention job. Skipped (False)
    when the row is no longer archived, is in the trash, is held, or its
    retention date is not reached."""
    row = await load(conn, table, doc_id, lock=True)
    today = today or datetime.now(timezone.utc).date()
    if (
        row is None
        or row["lifecycle_status"] != "archived"
        or row["deleted_at"] is not None
        or row["held"]
        or row["retention_until"] is None
        or row["retention_until"] > today
    ):
        return False
    check_transition(row["lifecycle_status"], "destruction_eligible", "retention")
    await conn.execute(
        text(
            f"UPDATE \"{table}\" SET lifecycle_status = 'destruction_eligible'"
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": doc_id},
    )
    return True


async def extend(conn, table: str, doc_id: str, until: date) -> dict:
    """destruction_eligible → archived, kept until `until` (a manual retention
    date that wins over the rules - retention_override). `until` must be in
    the future."""
    row = await _require(conn, table, doc_id)
    if until <= datetime.now(timezone.utc).date():
        raise LifecycleError(422, "lifecycle.extend_past")
    check_transition(row["lifecycle_status"], "archived", "extend")
    await conn.execute(
        text(f"""
            UPDATE "{table}" SET lifecycle_status = 'archived', retention_override = :d,
                   retention_until = :d
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": doc_id, "d": until},
    )
    return row


# ── Destruction ───────────────────────────────────────────────────────────────


async def tombstone(
    conn, table: str, doc_id: str, op: str, destruction_id: str
) -> dict:
    """Turn a document into its tombstone (lifecycle_status 'destroyed'):
    record_id, type, series, dates and hashes stay; every other extracted
    field and the row's file references are nulled. `op` is 'destruction'
    (approved record, the document must be destruction_eligible) or 'purge'
    (trash purge, the document must be in the trash past its retention).
    Refused under legal hold. The caller deletes the files, versions,
    pages, grants and tags (destruction.execute). Returns the old row."""
    row = await _require(conn, table, doc_id)
    if row["held"]:
        raise LifecycleError(409, "lifecycle.legal_hold")
    if op == "purge" and row["deleted_at"] is None:
        raise LifecycleError(409, "lifecycle.not_in_trash")
    check_transition(row["lifecycle_status"], "destroyed", op)

    from ingest_router import _BASE_COLS  # lazy - ingest_router imports this module

    columns = (
        (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
        )
        .scalars()
        .all()
    )
    nulled = [
        c
        for c in columns
        if (c not in _BASE_COLS and c not in _TOMBSTONE_KEEP)
        or c in _TOMBSTONE_FILE_COLS
    ] + ["locked_by", "locked_until"]
    sets = ", ".join(
        f'"{c}" = NULL' for c in sorted(set(nulled)) if _TABLE_RE.fullmatch(c)
    )
    await conn.execute(
        text(f"""
            UPDATE "{table}" SET {sets}, lifecycle_status = 'destroyed', destroyed_at = now(),
                   destruction_id = CAST(:did AS uuid)
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": doc_id, "did": destruction_id},
    )
    return row


# ── Legal hold flags ──────────────────────────────────────────────────────────


async def refresh_hold_flags(
    conn, tables: list[str], doc_ids: list[str] | None = None
) -> int:
    """Recompute the legal_hold column of every row of `tables` from the
    active holds - a copy kept for lists and badges; enforcement always
    evaluates held_sql. Called after a hold is set or released and after
    series assignments/moves. Returns the rows changed."""
    changed = 0
    for table in tables:
        _check_table(table)
        result = await conn.execute(
            text(f"""
                UPDATE "{table}" SET legal_hold = sub.h
                FROM (SELECT id, {held_sql(table)} AS h FROM "{table}") sub
                WHERE "{table}".id = sub.id AND "{table}".legal_hold IS DISTINCT FROM sub.h
                  AND COALESCE("{table}".lifecycle_status, 'active') <> 'destroyed'
                  {"AND sub.id = ANY(CAST(:ids AS uuid[]))" if doc_ids is not None else ""}
            """),
            {"ids": list(doc_ids or [])},
        )
        changed += result.rowcount or 0
    return changed
