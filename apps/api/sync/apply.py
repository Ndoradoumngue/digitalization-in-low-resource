"""Apply a peer's changes to this node (GED Phase 8.2 / 8.4).

Used by POST /api/sync/push (the peer sends its changes here) and by the
worker after a pull (this node fetched the peer's changes). Runs in the
caller's transaction with `SET LOCAL sdai.applying_sync = on`, so nothing
applied here is captured again (no echo), and the touch/audit-chain
triggers keep the values the rows arrived with.

Rules (see sync.capture for the row clock):
  ddl     run _ensure_table for the announced columns (per-type tables only)
  insert  new row as sent; an existing row is merged field by field
  update  per field, last writer wins by HLC: applied when the change is
          newer than the local clock of that field. When the change's base
          (the clock value it overwrote on the sender) differs from the local
          clock, both sides changed the field independently - if the values
          also differ, that is a conflict (reported, persisted by the caller).
  delete  the row goes, whatever was changed concurrently (delete wins)

Every change runs in its own savepoint: one that can't be applied (a unique
key taken by a different row, a missing parent) is reported as an error and
the rest still go through - sync never stalls on one bad row.
Identifiers are never taken from the payload unchecked: tables must be a
synced table or an existing tenant's per-type table, columns must exist
locally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import text

from sync.capture import SYNC_META_COLS, SYNC_TABLE_BY_NAME, SyncTable
from sync.hlc import floor_after, is_hlc, max_hlc

_DYNAMIC_TABLE_RE = re.compile(r"^t_[a-z][a-z0-9_]{0,23}_[a-z0-9_]{1,62}$")
_COLUMN_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_DDL_FIELD_VALUE = {"jsonb": [], "boolean": True}


@dataclass
class ApplyResult:
    applied: int = 0
    skipped: int = 0
    last_id: int = 0
    max_hlc: str | None = None
    tables: set[str] = field(default_factory=set)
    conflicts: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    # (table, pk, field) the peer changed knowing our current value - e.g.
    # the resolution of a conflict made over there: closes our open one.
    superseded: list[tuple[str, dict, str]] = field(default_factory=list)


class ApplyError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _q(name: str) -> str:
    return f'"{name}"'


async def _table_spec(conn, table: str) -> SyncTable:
    spec = SYNC_TABLE_BY_NAME.get(table)
    if spec:
        return spec
    if _DYNAMIC_TABLE_RE.match(table):
        tenant_ok = (
            await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sdai_tenants WHERE starts_with(:t, 't_' || slug || '_'))"
                ),
                {"t": table},
            )
        ).scalar()
        if tenant_ok:
            return SyncTable(table)
    raise ApplyError("unknown_table", f"Not a synced table: {table!r}")


async def _columns(conn, table: str, cache: dict) -> dict[str, str]:
    """The table's writable columns. Generated ones are left out: each
    node computes them itself (a peer on older code may still send one -
    it is dropped, see _generated)."""
    if table not in cache:
        rows = await conn.execute(
            text(
                "SELECT column_name, data_type, is_generated <> 'NEVER'"
                " FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
        rows = rows.all()
        cache[table] = {r[0]: r[1] for r in rows if not r[2]}
        cache[("generated", table)] = {r[0] for r in rows if r[2]}
    return cache[table]


def _generated(table: str, cache: dict) -> set[str]:
    return cache.get(("generated", table), set())


def _pk_where(spec: SyncTable, pk: dict, alias: str = "") -> str:
    """WHERE clause matching the row whose primary key is the :pk parameter
    (typed through the table's own row type)."""
    if not isinstance(pk, dict) or set(pk) != set(spec.pk):
        raise ApplyError("bad_pk", f"Primary key does not match {list(spec.pk)}")
    prefix = f"{alias}." if alias else ""
    lhs = ", ".join(prefix + _q(c) for c in spec.pk)
    rhs = ", ".join(_q(c) for c in spec.pk)
    return f"({lhs}) = (SELECT {rhs} FROM jsonb_populate_record(NULL::{_q(spec.name)}, CAST(:pk AS jsonb)))"


# The row clock is keyed by the primary key's jsonb text - always produced
# by PostgreSQL (as in the capture trigger), never by json.dumps, whose key
# order and spacing differ.
_PK_TEXT = "CAST(CAST(:pk AS jsonb) AS text)"


async def _apply_ddl(conn, change: dict, spec: SyncTable) -> None:
    import ingest_router

    if spec.name in SYNC_TABLE_BY_NAME:
        raise ApplyError("bad_ddl", "ddl events only exist for per-type tables")
    columns = (change.get("row_data") or {}).get("columns") or {}
    fields = {
        col: _DDL_FIELD_VALUE.get(pg_type, "")
        for col, pg_type in columns.items()
        if _COLUMN_RE.match(col)
        and col not in ingest_router._BASE_COLS
        and col not in ("record_id", "series_id", "uploaded_by")
    }
    await ingest_router._ensure_table(conn, spec.name, fields)


async def _clock(conn, table: str, pk: dict) -> dict | None:
    return (
        await conn.execute(
            text(
                f"SELECT field_hlcs FROM sdai_sync_row_clock WHERE table_name = :t AND pk = {_PK_TEXT}"
            ),
            {"t": table, "pk": json.dumps(pk)},
        )
    ).scalar()


async def _set_clock(
    conn, table: str, pk: dict, fields: dict, replace: bool = False
) -> None:
    merge = (
        "EXCLUDED.field_hlcs"
        if replace
        else "sdai_sync_row_clock.field_hlcs || EXCLUDED.field_hlcs"
    )
    await conn.execute(
        text(f"""
            INSERT INTO sdai_sync_row_clock (table_name, pk, field_hlcs)
            VALUES (:t, {_PK_TEXT}, CAST(:f AS jsonb))
            ON CONFLICT (table_name, pk) DO UPDATE SET field_hlcs = {merge}
        """),
        {"t": table, "pk": json.dumps(pk), "f": json.dumps(fields)},
    )


async def _current_row(conn, spec: SyncTable, pk: dict) -> dict | None:
    return (
        await conn.execute(
            text(
                f"SELECT to_jsonb(r) FROM {_q(spec.name)} r WHERE {_pk_where(spec, pk)}"
            ),
            {"pk": json.dumps(pk)},
        )
    ).scalar()


async def _normalized(conn, table: str, data: dict) -> dict:
    """The sent values as this database would store and print them, so they
    compare equal to the local row's when they are the same."""
    return (
        await conn.execute(
            text(
                f"SELECT to_jsonb(p) FROM jsonb_populate_record(NULL::{_q(table)}, CAST(:d AS jsonb)) p"
            ),
            {"d": json.dumps(data)},
        )
    ).scalar()


def _insert_clock(change: dict) -> dict:
    """Field clocks an insert carries: {"*": its stamp} for a row captured
    as it was inserted, or - for a row re-sent by a (re)seed - the
    sender's whole row clock in base_hlcs, so that a merge onto an existing
    row compares each field's real last-change stamp."""
    base = change.get("base_hlcs") if isinstance(change.get("base_hlcs"), dict) else {}
    return {"*": change["hlc"], **{k: v for k, v in base.items() if is_hlc(v)}}


async def _merge_fields(
    conn,
    change: dict,
    spec: SyncTable,
    cols: dict,
    row: dict,
    data: dict,
    stamps: dict,
    base: dict | None,
    result: ApplyResult,
) -> bool:
    """Field-level last-writer-wins of `data` onto the existing `row`.
    stamps: the incoming field clocks ("*" for fields not listed). base:
    for an update, the clocks it overwrote on the sender - a field whose
    base differs from the local clock was changed on both sides
    independently (a conflict when the values differ); None for a merged
    insert, which only ever fast-forwards."""
    pk = change["pk"]
    clock = await _clock(conn, spec.name, pk) or {}
    remote = await _normalized(conn, spec.name, data)
    winners: dict[str, str] = {}
    for col in data:
        if col in spec.pk or col in SYNC_META_COLS:
            continue
        local_stamp = clock.get(col) or clock.get("*")
        remote_stamp = stamps.get(col) or stamps["*"]
        remote_newer = local_stamp is None or remote_stamp > local_stamp
        if base is not None and base.get(col) == local_stamp and remote_newer:
            result.superseded.append((spec.name, pk, col))
        if (
            base is not None
            and base.get(col) != local_stamp
            and remote.get(col) != row.get(col)
        ):
            result.conflicts.append(
                {
                    "table_name": spec.name,
                    "pk": pk,
                    "field": col,
                    "local_value": row.get(col),
                    "remote_value": remote.get(col),
                    "local_hlc": local_stamp,
                    "remote_hlc": remote_stamp,
                    "remote_node": change["origin_node"],
                    "winner": "remote" if remote_newer else "local",
                }
            )
        if remote_newer:
            winners[col] = remote_stamp
    if not winners:
        return False
    assignments = [f"{_q(c)} = p.{_q(c)}" for c in winners]
    params = {
        "pk": json.dumps(pk),
        "d": json.dumps(data),
        "h": max(winners.values()),
        "o": change["origin_node"],
    }
    if "hlc" in cols:
        assignments.append("hlc = GREATEST(COALESCE(t.hlc, ''), :h)")
    if "origin_node" in cols:
        assignments.append("origin_node = :o")
    if "updated_at" in cols:
        assignments.append("updated_at = now()")
    await conn.execute(
        text(
            f"UPDATE {_q(spec.name)} t SET {', '.join(assignments)}"
            f" FROM jsonb_populate_record(NULL::{_q(spec.name)}, CAST(:d AS jsonb)) p"
            f" WHERE {_pk_where(spec, pk, 't')}"
        ),
        params,
    )
    await _set_clock(conn, spec.name, pk, winners)
    return True


async def _apply_one(conn, change: dict, cache: dict, result: ApplyResult) -> bool:
    op = change.get("op")
    if op not in ("insert", "update", "delete", "ddl"):
        raise ApplyError("bad_op", f"Unknown op {op!r}")
    if not is_hlc(change.get("hlc")):
        raise ApplyError("bad_hlc", "Missing or malformed hlc")
    table = change.get("table_name") or ""
    spec = await _table_spec(conn, table)
    if op == "ddl":
        await _apply_ddl(conn, change, spec)
        cache.pop(table, None)
        return True

    cols = await _columns(conn, table, cache)
    if not cols:
        raise ApplyError("unknown_table", f"Table {table!r} does not exist here")
    pk = change.get("pk") or {}
    if spec.insert_only and op != "insert":
        raise ApplyError("bad_op", f"{table} only takes inserts")

    if op == "delete":
        deleted = await conn.execute(
            text(f"DELETE FROM {_q(table)} WHERE {_pk_where(spec, pk)}"),
            {"pk": json.dumps(pk)},
        )
        await conn.execute(
            text(
                f"DELETE FROM sdai_sync_row_clock WHERE table_name = :t AND pk = {_PK_TEXT}"
            ),
            {"t": table, "pk": json.dumps(pk)},
        )
        return deleted.rowcount > 0

    sent = change.get("row_data") or {}
    if not isinstance(sent, dict):
        raise ApplyError("bad_change", "row_data must be an object")
    unknown = [
        c
        for c in sent
        if c not in cols and c not in SYNC_META_COLS and c not in _generated(table, cache)
    ]
    if unknown and op == "update":
        # A column this node doesn't have yet (its ddl event failed or the
        # peer runs newer code) - applying the rest would lose that value.
        raise ApplyError(
            "unknown_column", f"Unknown columns on {table}: {sorted(unknown)}"
        )
    data = {c: v for c, v in sent.items() if c in cols and _COLUMN_RE.match(c)}
    row = await _current_row(conn, spec, pk)

    if op == "update":
        if row is None:
            return False  # deleted here meanwhile - delete wins
        base = (
            change.get("base_hlcs") if isinstance(change.get("base_hlcs"), dict) else {}
        )
        return await _merge_fields(
            conn, change, spec, cols, row, data, {"*": change["hlc"]}, base, result
        )

    if row is not None:
        if spec.insert_only:
            return False  # already here (re-sent page)
        return await _merge_fields(
            conn, change, spec, cols, row, data, _insert_clock(change), None, result
        )
    if "hlc" in cols:
        data["hlc"] = change["hlc"]
    if "origin_node" in cols:
        data["origin_node"] = change["origin_node"]
    names = ", ".join(_q(c) for c in data)
    await conn.execute(
        text(
            f"INSERT INTO {_q(table)} ({names})"
            f" SELECT {names} FROM jsonb_populate_record(NULL::{_q(table)}, CAST(:d AS jsonb))"
        ),
        {"d": json.dumps(data)},
    )
    if not spec.insert_only:
        await _set_clock(conn, table, pk, _insert_clock(change), replace=True)
    return True


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, ApplyError):
        return exc.kind
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
        getattr(getattr(exc, "orig", None), "__cause__", None), "sqlstate", None
    )
    return {"23505": "unique_violation", "23503": "missing_parent"}.get(
        sqlstate or "", "apply_failed"
    )


async def apply_changes(conn, changes: list[dict]) -> ApplyResult:
    """Apply `changes` (in the order given - the sender's changelog order)
    inside the caller's transaction. Raises nothing for a bad change: it is
    reported in result.errors and skipped."""
    result = ApplyResult()
    await conn.execute(text("SET LOCAL sdai.applying_sync = on"))
    cache: dict = {}
    for change in changes:
        if not isinstance(change, dict):
            result.errors.append(
                {"kind": "bad_change", "message": "not an object", "change": None}
            )
            continue
        try:
            async with conn.begin_nested():
                changed = await _apply_one(conn, change, cache, result)
        except Exception as exc:  # noqa: BLE001 - reported, never stalls sync
            result.errors.append(
                {"kind": _error_kind(exc), "message": str(exc)[:1000], "change": change}
            )
            changed = False
        if changed:
            result.applied += 1
            result.tables.add(change.get("table_name"))
        else:
            result.skipped += 1
        if isinstance(change.get("id"), int):
            result.last_id = max(result.last_id, change["id"])
        if is_hlc(change.get("hlc")):
            result.max_hlc = max_hlc(result.max_hlc, change["hlc"])
    if result.max_hlc:
        # Receive rule: local stamps from now on sort after everything seen.
        await conn.execute(
            text(
                "UPDATE sdai_sync_state SET hlc_floor_ms = GREATEST(hlc_floor_ms, :f) WHERE id = 1"
            ),
            {"f": floor_after(result.max_hlc)},
        )
    return result
