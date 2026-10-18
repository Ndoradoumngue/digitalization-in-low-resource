"""Moving a document into its detected type (GED Phase 10) - the ONLY code
that moves a document row from one per-type table to another.

A plain upload is filed under the generic type (`<tenant>_document`, review
status 'not_extracted'). When it is extracted later and the model detects
a type, move_document puts it into that type's table: the row keeps its id,
record_id, category, lifecycle, lock and file columns, and every row that
names the document by (table, id) is re-pointed in the same transaction,
so versions, grants, tags, links, comments, signatures, share links,
notifications, legal holds and sync conflicts follow it. Sync replays the
move like any other change (ddl for a new table, insert, updates, delete).

The audit log is append-only and never rewritten: per-document audit
queries match on document_id alone (ids are UUIDs, unique across tables)
and the move itself is audited (document_type_assigned, from/to).

No router imports at module level (ingest_router imports this module).
"""

from __future__ import annotations

import re

from sqlalchemy import text

_TABLE_RE = re.compile(r"^[a-z0-9_]+$")

# (table, table column, id column) of every row that names a document.
REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("sdai_document_access", "table_name", "document_id"),
    ("sdai_document_tags", "table_name", "document_id"),
    ("sdai_document_versions", "table_name", "document_id"),
    ("sdai_field_history", "table_name", "document_id"),
    ("sdai_comments", "table_name", "document_id"),
    ("sdai_notifications", "table_name", "document_id"),
    ("sdai_share_links", "table_name", "document_id"),
    ("sdai_signature_requests", "table_name", "document_id"),
    ("sdai_signatures", "table_name", "document_id"),
    ("sdai_legal_hold_documents", "table_name", "document_id"),
    ("sdai_sync_conflicts", "table_name", "document_id"),
    ("sdai_document_links", "from_table", "from_id"),
    ("sdai_document_links", "to_table", "to_id"),
    ("batch_documents", "target_table", "target_document_id"),
)


def _check(table: str) -> str:
    if not _TABLE_RE.fullmatch(table):
        raise ValueError(f"bad table name {table!r}")
    return table


async def _columns(conn, table: str) -> list[str]:
    rows = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = :t"
            " AND is_generated = 'NEVER' ORDER BY ordinal_position"
        ),
        {"t": table},
    )
    return [r[0] for r in rows]


async def move_document(
    conn, from_table: str, doc_id: str, to_table: str, fields: dict
) -> None:
    """Move one document row from `from_table` into `to_table` (created or
    widened for `fields` first), inside the caller's transaction. Columns
    the new table lacks are dropped - a plain document has no field values."""
    from ingest_router import _ensure_table

    _check(from_table)
    _check(to_table)
    if from_table == to_table:
        return
    await _ensure_table(conn, to_table, fields)
    target_cols = set(await _columns(conn, to_table))
    cols = [c for c in await _columns(conn, from_table) if c in target_cols]
    col_list = ", ".join(f'"{c}"' for c in cols)
    moved = await conn.execute(
        text(
            f'INSERT INTO "{to_table}" ({col_list})'
            f' SELECT {col_list} FROM "{from_table}" WHERE id = CAST(:id AS uuid)'
        ),
        {"id": doc_id},
    )
    if moved.rowcount != 1:
        raise LookupError(f"document {doc_id} not found in {from_table}")
    for table, table_col, id_col in REFERENCES:
        await conn.execute(
            text(
                f'UPDATE "{table}" SET "{table_col}" = :to'
                f' WHERE "{table_col}" = :frm AND "{id_col}" = CAST(:id AS uuid)'
            ),
            {"to": to_table, "frm": from_table, "id": doc_id},
        )
    await conn.execute(
        text(f'DELETE FROM "{from_table}" WHERE id = CAST(:id AS uuid)'), {"id": doc_id}
    )
