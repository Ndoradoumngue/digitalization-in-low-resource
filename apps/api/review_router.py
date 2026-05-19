"""
Human review queue — serves review_required documents and accepts
approve / reject / flag actions.

Endpoints:
  GET   /api/review/queue              — paginated queue, oldest first
  GET   /api/review/count              — pending count (sidebar badge)
  PATCH /api/review/{table}/{id}       — approve (+ field corrections) or reject
  POST  /api/review/{table}/{id}/flag  — escalate to manual_entry
"""

import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from audit import client_ip, log_action
from auth import CurrentUser, get_current_user
from cache import _path_key_builder, invalidate_cache
from fastapi_cache.decorator import cache
from rate_limit import limiter
from documents_router import _get_tables_columns, _sanitize
from ingest_router import _engine

# ── Constants ─────────────────────────────────────────────────────────────────

_OPTIONAL_COLS = [
    "document_type", "reference_number", "date",
    "organisation", "destination_or_subject", "signatory",
]

# Fields the reviewer is not allowed to overwrite
_PROTECTED = frozenset({
    "id", "ingested_at", "review_status", "source_image_path",
    "table_name", "batch_id",
})

# ── Row serialisation helper ──────────────────────────────────────────────────

def _serial(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


# ── Queue query helpers ───────────────────────────────────────────────────────

def _queue_select(table: str, cols: set[str]) -> str:
    """Build one UNION branch for review_required rows in `table`."""
    sel = ["id::text", f"'{table}'::text AS table_name"]
    for col in _OPTIONAL_COLS:
        sel.append(f"{col}::text" if col in cols else f"NULL::text AS {col}")
    sel += [
        "confidence::text     AS confidence",
        "review_status::text  AS review_status",
        "ingested_at::text    AS ingested_at",
        "source_image_path",
    ]
    return (
        f"SELECT {', '.join(sel)}\n"
        f"FROM \"{table}\"\n"
        f"WHERE review_status = 'review_required'"
    )


async def _run_queue(
    conn,
    tables_cols: dict,
    *,
    only_table: Optional[str],
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    scope = (
        {only_table: tables_cols[only_table]}
        if only_table and only_table in tables_cols
        else tables_cols
    )
    if not scope:
        return [], 0

    parts = [_queue_select(tbl, cols) for tbl, cols in scope.items()]
    union = "\nUNION ALL\n".join(parts)

    sql = f"""
        WITH q AS ({union})
        SELECT *, COUNT(*) OVER() AS total_count
        FROM q
        ORDER BY ingested_at ASC NULLS LAST
        LIMIT :page_size OFFSET :offset
    """
    result = await conn.execute(
        text(sql),
        {"page_size": page_size, "offset": (page - 1) * page_size},
    )
    rows  = result.mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0
    return [
        {k: v for k, v in dict(r).items() if k != "total_count"}
        for r in rows
    ], total


async def _pending_count(conn, tables_cols: dict) -> int:
    if not tables_cols:
        return 0
    parts = [
        f"SELECT COUNT(*) AS n FROM \"{t}\" WHERE review_status = 'review_required'"
        for t in tables_cols
    ]
    union  = "\nUNION ALL\n".join(parts)
    result = await conn.execute(text(f"SELECT COALESCE(SUM(n), 0) FROM ({union}) sub"))
    return int(result.scalar() or 0)


# ── Pydantic bodies ───────────────────────────────────────────────────────────

class ReviewPatch(BaseModel):
    fields: dict[str, object] = {}   # corrected field values (str | list | None)
    action: str                       # "approve" | "reject"


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/count", summary="Pending review count")
@cache(expire=10, namespace="review", key_builder=_path_key_builder)
async def review_count(current_user: CurrentUser = Depends(get_current_user)):
    """
    Return the total number of documents with `review_status = review_required`
    across all ingest-created tables. Called on dashboard load to populate the
    sidebar badge without fetching the full queue.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
        return {"pending": await _pending_count(conn, tables_cols)}


@router.get("/queue", summary="List pending review items")
async def review_queue(
    page:          int           = Query(1,  ge=1),
    page_size:     int           = Query(20, ge=1, le=100),
    document_type: Optional[str] = None,
    current_user:  CurrentUser   = Depends(get_current_user),
):
    """
    Return documents with `review_status = review_required`, ordered oldest-first
    so that the longest-waiting items are presented to reviewers first.
    Each item includes `table_name` — use it to construct the PATCH URL for
    submitting field corrections: `PATCH /api/review/{table_name}/{id}`.
    Optional `document_type` query parameter restricts results to one table.
    """
    only_table = _sanitize(document_type) if document_type else None

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
        if only_table and only_table not in tables_cols:
            return {"total": 0, "items": []}

        items, total = await _run_queue(
            conn, tables_cols,
            only_table=only_table,
            page=page,
            page_size=page_size,
        )

    return {"total": total, "items": items}


@router.patch("/{table_name}/{doc_id}")
@limiter.limit("60/minute")
async def patch_review(
    request:      Request,
    table_name:   str,
    doc_id:       str,
    body:         ReviewPatch,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Approve or reject a document.
    - approve: writes corrected fields, sets review_status='approved', reviewed_at=now()
    - reject:  sets review_status='rejected'
    """
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'")

    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=f"Table '{safe}' not found")

        cols = tables_cols[safe]

        # Ensure audit columns exist on every action (idempotent DDL)
        ddl_needed = False
        if "reviewed_at" not in cols:
            await conn.execute(text(
                f'ALTER TABLE "{safe}" ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ'
            ))
            ddl_needed = True
        if "reviewed_by" not in cols:
            await conn.execute(text(
                f'ALTER TABLE "{safe}" ADD COLUMN IF NOT EXISTS reviewed_by UUID'
            ))
            ddl_needed = True
        if ddl_needed:
            await conn.commit()

        if body.action == "approve":
            # Fetch column data types to handle JSONB correctly
            type_rows = await conn.execute(text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :t
            """), {"t": safe})
            col_types = {r[0]: r[1] for r in type_rows}

            # Build SET clause — only valid, non-protected columns
            editable = {
                k: v for k, v in body.fields.items()
                if k in cols and k not in _PROTECTED
            }

            set_parts: list[str] = []
            params: dict = {"doc_id": doc_id}

            for col, val in editable.items():
                pname    = f"v_{col}"
                col_type = col_types.get(col, "text")
                is_jsonb = col_type in ("json", "jsonb")

                if val is None or val == "":
                    set_parts.append(f'"{col}" = :{pname}')
                    params[pname] = None
                elif is_jsonb and isinstance(val, list):
                    set_parts.append(f'"{col}" = :{pname}::jsonb')
                    params[pname] = json.dumps(val)
                elif is_jsonb and isinstance(val, str):
                    # Comma-separated string → JSON array
                    arr = [s.strip() for s in val.split(",") if s.strip()]
                    set_parts.append(f'"{col}" = :{pname}::jsonb')
                    params[pname] = json.dumps(arr)
                else:
                    set_parts.append(f'"{col}" = :{pname}')
                    params[pname] = str(val)

            set_parts += [
                "review_status = 'approved'",
                "reviewed_at   = NOW()",
                "reviewed_by   = :reviewed_by_id::uuid",
            ]
            params["reviewed_by_id"] = current_user.id

            sql = (
                f'UPDATE "{safe}" SET {", ".join(set_parts)} '
                f"WHERE id = :doc_id::uuid RETURNING *"
            )

        else:  # reject
            sql    = (
                f"UPDATE \"{safe}\""
                f" SET review_status = 'rejected',"
                f"     reviewed_by   = :reviewed_by_id::uuid"
                f" WHERE id = :doc_id::uuid RETURNING *"
            )
            params = {"doc_id": doc_id, "reviewed_by_id": current_user.id}

        result = await conn.execute(text(sql), params)
        await conn.commit()

    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

    audit_action = "document_approved" if body.action == "approve" else "document_rejected"
    audit_details: dict = {"confidence": dict(row).get("confidence")}
    if body.action == "approve":
        audit_details["fields_changed"] = list(editable.keys())
    await log_action(
        action=audit_action,
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details=audit_details,
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")

    return {k: _serial(v) for k, v in dict(row).items()}


@router.post("/{table_name}/{doc_id}/flag")
@limiter.limit("60/minute")
async def flag_review(
    request:      Request,
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Escalate a document to manual_entry — removes it from the review queue."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=f"Table '{safe}' not found")

        result = await conn.execute(
            text(
                f"UPDATE \"{safe}\" SET review_status = 'manual_entry' "
                f"WHERE id = :doc_id::uuid RETURNING id"
            ),
            {"doc_id": doc_id},
        )
        await conn.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    await log_action(
        action="document_flagged",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")

    return {"ok": True}
