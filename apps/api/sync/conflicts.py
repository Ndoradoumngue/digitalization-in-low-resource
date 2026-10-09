"""Sync conflicts (GED Phase 8.4): recording what an apply could not settle
on its own, and resolving it.

A field conflict (the same field changed on both nodes independently) is
already settled by last-writer-wins when it is recorded - both nodes hold
the winning value. The record keeps both values so nothing is lost
silently: the document shows in the review queue ("conflit de
synchronisation") and in the admin Synchronisation page until an admin
keeps the winner, restores the other value or types a new one. A failed
change (unique key taken, missing parent, ...) is kept with its payload and
can be retried once the cause is fixed, or dismissed.

Resolving writes an ordinary, captured edit - it reaches the peer like any
other change, and the peer closes its own copy of the conflict when that
edit arrives (ApplyResult.superseded).
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text

from sync.apply import (
    _DYNAMIC_TABLE_RE,
    ApplyError,
    ApplyResult,
    _columns,
    _pk_where,
    _q,
    _table_spec,
    apply_changes,
)

FIELD = "field"
RESOLUTIONS = ("keep_local", "keep_remote", "manual", "retry", "dismiss")


async def _tenant_of(
    conn, table: str, pk: dict, values: dict | None = None
) -> str | None:
    """The tenant a conflicting row belongs to, when it can be told: per-type
    tables by their t_<slug>_ prefix (longest slug wins), other tables by a
    tenant_id column (or their batch's), else None (deployment-wide)."""
    if _DYNAMIC_TABLE_RE.match(table or ""):
        return (
            await conn.execute(
                text(
                    "SELECT id::text FROM sdai_tenants WHERE starts_with(:t, 't_' || slug || '_')"
                    " ORDER BY length(slug) DESC LIMIT 1"
                ),
                {"t": table},
            )
        ).scalar()
    values = values or {}
    if isinstance(values.get("tenant_id"), str):
        return values["tenant_id"]
    try:
        spec = await _table_spec(conn, table)
        row = (
            await conn.execute(
                text(
                    f"SELECT to_jsonb(r) FROM {_q(table)} r WHERE {_pk_where(spec, pk)}"
                ),
                {"pk": json.dumps(pk)},
            )
        ).scalar()
    except Exception:  # noqa: BLE001 - best effort, the conflict is kept anyway
        return None
    row = row or values
    if row.get("tenant_id"):
        return row["tenant_id"]
    batch_id = row.get("batch_id")
    if batch_id is None and row.get("batch_document_id"):
        batch_id = (
            await conn.execute(
                text(
                    "SELECT batch_id::text FROM batch_documents WHERE id = CAST(:i AS uuid)"
                ),
                {"i": row["batch_document_id"]},
            )
        ).scalar()
    if batch_id:
        return (
            await conn.execute(
                text("SELECT tenant_id::text FROM batches WHERE id = CAST(:i AS uuid)"),
                {"i": batch_id},
            )
        ).scalar()
    return None


def _document_id(table: str, pk: dict) -> str | None:
    return (
        pk.get("id")
        if _DYNAMIC_TABLE_RE.match(table or "") and isinstance(pk, dict)
        else None
    )


async def record_outcome(conn, peer: str, result: ApplyResult) -> None:
    """In the apply transaction: persist conflicts and failed changes, write
    the field history of each conflict, close conflicts the peer superseded.
    Audit entries follow after commit (sync.store.after_apply)."""
    for c in result.conflicts:
        tenant_id = await _tenant_of(conn, c["table_name"], c["pk"])
        document_id = _document_id(c["table_name"], c["pk"])
        await conn.execute(
            text("""
                INSERT INTO sdai_sync_conflicts
                    (kind, tenant_id, table_name, pk, document_id, field, local_value, remote_value,
                     local_hlc, remote_hlc, remote_node, winner)
                VALUES ('field', CAST(:tid AS uuid), :t, CAST(:pk AS jsonb), CAST(:doc AS uuid), :f,
                        CAST(:lv AS jsonb), CAST(:rv AS jsonb), :lh, :rh, :node, :w)
                ON CONFLICT (table_name, (pk::text), field) WHERE status = 'open' AND kind = 'field'
                DO UPDATE SET local_value = EXCLUDED.local_value, remote_value = EXCLUDED.remote_value,
                    local_hlc = EXCLUDED.local_hlc, remote_hlc = EXCLUDED.remote_hlc,
                    winner = EXCLUDED.winner, occurrences = sdai_sync_conflicts.occurrences + 1,
                    updated_at = now()
            """),
            {
                "tid": tenant_id,
                "t": c["table_name"],
                "pk": json.dumps(c["pk"]),
                "doc": document_id,
                "f": c["field"],
                "lv": json.dumps(c["local_value"]),
                "rv": json.dumps(c["remote_value"]),
                "lh": c["local_hlc"],
                "rh": c["remote_hlc"],
                "node": c["remote_node"],
                "w": c["winner"],
            },
        )
        c["tenant_id"] = tenant_id
        if document_id and tenant_id:
            # What sync did to the document: the losing value replaced by
            # the winning one (on the side that held the loser).
            lost, kept = (
                (c["local_value"], c["remote_value"])
                if c["winner"] == "remote"
                else (c["remote_value"], c["local_value"])
            )
            await conn.execute(
                text("""
                    INSERT INTO sdai_field_history
                        (tenant_id, table_name, document_id, field, old_value, new_value, change_source)
                    VALUES (CAST(:tid AS uuid), :t, CAST(:doc AS uuid), :f,
                            CAST(:old AS jsonb), CAST(:new AS jsonb), 'sync')
                """),
                {
                    "tid": tenant_id,
                    "t": c["table_name"],
                    "doc": document_id,
                    "f": c["field"],
                    "old": json.dumps(lost),
                    "new": json.dumps(kept),
                },
            )
    for e in result.errors:
        change = e.get("change") if isinstance(e.get("change"), dict) else {}
        table = (
            change.get("table_name")
            if isinstance(change.get("table_name"), str)
            else None
        )
        pk = change.get("pk") if isinstance(change.get("pk"), dict) else None
        tenant_id = None
        if table and pk and e["kind"] not in ("unknown_table", "bad_pk"):
            tenant_id = await _tenant_of(
                conn,
                table,
                pk,
                change.get("row_data")
                if isinstance(change.get("row_data"), dict)
                else None,
            )
        e["tenant_id"] = tenant_id
        await conn.execute(
            text("""
                INSERT INTO sdai_sync_conflicts
                    (kind, tenant_id, table_name, pk, document_id, remote_hlc, remote_node, change, message)
                VALUES (:k, CAST(:tid AS uuid), :t, CAST(:pk AS jsonb), CAST(:doc AS uuid), :rh, :node,
                        CAST(:change AS jsonb), :msg)
            """),
            {
                "k": e["kind"],
                "tid": tenant_id,
                "t": table,
                "pk": json.dumps(pk) if pk is not None else None,
                "doc": _document_id(table, pk or {})
                if _is_uuid((pk or {}).get("id"))
                else None,
                "rh": change.get("hlc") if isinstance(change.get("hlc"), str) else None,
                "node": peer,
                "change": json.dumps(change, default=str),
                "msg": e["message"],
            },
        )
    open_fields = (
        result.superseded
        and (
            await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sdai_sync_conflicts WHERE status = 'open' AND kind = 'field')"
                )
            )
        ).scalar()
    )
    for table, pk, col in result.superseded if open_fields else ():
        await conn.execute(
            text("""
                UPDATE sdai_sync_conflicts
                SET status = 'resolved', resolution = 'superseded', resolved_at = now(), updated_at = now()
                WHERE status = 'open' AND kind = 'field'
                  AND table_name = :t AND pk = CAST(:pk AS jsonb) AND field = :f
            """),
            {"t": table, "pk": json.dumps(pk), "f": col},
        )


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


async def audit_outcome(result: ApplyResult | None, peer: str) -> None:
    """After commit: one sync_conflict audit entry per conflict or failed
    change (written by log_action on its own connection, so it is captured
    and reaches the peer like every audit row - the chains stay whole)."""
    if result is None:
        return
    from audit import log_action

    for c in result.conflicts:
        await log_action(
            action="sync_conflict",
            table_name=c["table_name"]
            if _DYNAMIC_TABLE_RE.match(c["table_name"])
            else None,
            document_id=_document_id(c["table_name"], c["pk"]),
            details={
                "kind": FIELD,
                "table": c["table_name"],
                "field": c["field"],
                "winner": c["winner"],
                "remote_node": c["remote_node"],
                "tenant_id": c.get("tenant_id"),
            },
        )
    for e in result.errors:
        change = e.get("change") if isinstance(e.get("change"), dict) else {}
        await log_action(
            action="sync_conflict",
            details={
                "kind": e["kind"],
                "table": change.get("table_name"),
                "remote_node": peer,
                "message": e["message"][:300],
                "tenant_id": e.get("tenant_id"),
            },
        )


# ── Resolution ────────────────────────────────────────────────────────────────


class ResolveError(Exception):
    def __init__(self, status: int, key: str):
        super().__init__(key)
        self.status = status
        self.key = key


async def resolve(conn, conflict: dict, action: str, value, user_id: str) -> dict:
    """Settle one open conflict in the caller's transaction. Field conflicts:
    keep_local / keep_remote / manual (value) write that value as a normal
    edit when it differs from the current one, dismiss just closes. Failed
    changes: retry re-applies the stored change, dismiss closes.
    Returns {"resolution", "written"}."""
    if conflict["status"] != "open":
        raise ResolveError(409, "sync.conflict_not_open")
    if action not in RESOLUTIONS:
        raise ResolveError(400, "sync.bad_resolution")
    written = False
    if conflict["kind"] == FIELD:
        if action == "retry":
            raise ResolveError(400, "sync.bad_resolution")
        if action != "dismiss":
            chosen = {
                "keep_local": conflict["local_value"],
                "keep_remote": conflict["remote_value"],
                "manual": value,
            }[action]
            written = await _write_field(conn, conflict, chosen, user_id)
    elif action == "retry":
        result = await apply_changes(conn, [conflict["change"]])
        # Back to normal capture for anything else in this transaction.
        await conn.execute(text("SET LOCAL sdai.applying_sync = off"))
        if result.errors:
            raise ResolveError(409, "sync.retry_failed")
        written = result.applied > 0
    elif action != "dismiss":
        raise ResolveError(400, "sync.bad_resolution")
    await conn.execute(
        text("""
            UPDATE sdai_sync_conflicts
            SET status = 'resolved', resolution = :r, resolved_by = CAST(:u AS uuid),
                resolved_at = now(), updated_at = now()
            WHERE id = CAST(:id AS uuid)
        """),
        {"r": action, "u": user_id, "id": str(conflict["id"])},
    )
    return {"resolution": action, "written": written}


async def _write_field(conn, conflict: dict, chosen, user_id: str) -> bool:
    table, pk, col = conflict["table_name"], conflict["pk"], conflict["field"]
    try:
        spec = await _table_spec(conn, table)
    except ApplyError as exc:
        raise ResolveError(404, "sync.conflict_row_gone") from exc
    cols = await _columns(conn, table, {})
    if col not in cols:
        raise ResolveError(404, "sync.conflict_row_gone")
    current = (
        await conn.execute(
            text(
                f"SELECT to_jsonb(r) -> :c FROM {_q(table)} r WHERE {_pk_where(spec, pk)}"
            ),
            {"c": col, "pk": json.dumps(pk)},
        )
    ).one_or_none()
    if current is None:
        raise ResolveError(404, "sync.conflict_row_gone")
    normalized = (
        await conn.execute(
            text(
                f"SELECT to_jsonb(p) -> :c FROM jsonb_populate_record(NULL::{_q(table)}, CAST(:d AS jsonb)) p"
            ),
            {"c": col, "d": json.dumps({col: chosen})},
        )
    ).scalar()
    if normalized == current[0]:
        return False
    await conn.execute(
        text(
            f"UPDATE {_q(table)} t SET {_q(col)} = p.{_q(col)}"
            f" FROM jsonb_populate_record(NULL::{_q(table)}, CAST(:d AS jsonb)) p"
            f" WHERE {_pk_where(spec, pk, 't')}"
        ),
        {"d": json.dumps({col: chosen}), "pk": json.dumps(pk)},
    )
    document_id = _document_id(table, pk)
    if document_id and conflict.get("tenant_id"):
        await conn.execute(
            text("""
                INSERT INTO sdai_field_history
                    (tenant_id, table_name, document_id, field, old_value, new_value, changed_by,
                     change_source)
                VALUES (CAST(:tid AS uuid), :t, CAST(:doc AS uuid), :f, CAST(:old AS jsonb),
                        CAST(:new AS jsonb), CAST(:u AS uuid), 'sync')
            """),
            {
                "tid": str(conflict["tenant_id"]),
                "t": table,
                "doc": document_id,
                "f": col,
                "old": json.dumps(current[0]),
                "new": json.dumps(normalized),
                "u": user_id,
            },
        )
    return True
