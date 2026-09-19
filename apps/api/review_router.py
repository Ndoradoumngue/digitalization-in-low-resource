"""
Human review queue — serves review_required documents and accepts
approve / reject / flag actions.

Endpoints:
  GET    /api/review/queue              — paginated queue, oldest first
  GET    /api/review/count              — pending count (sidebar badge)
  PATCH  /api/review/{table}/{id}       — approve (+ field corrections) or reject
  POST   /api/review/{table}/{id}/flag  — escalate to manual_entry
  POST   /api/review/{table}/{id}/retry — re-run VLM on a crashed (manual_entry) document
  DELETE /api/review/{table}/{id}       — permanently remove a document (admin only)
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from audit import client_ip, log_action
from auth import CurrentUser, get_current_user, require_admin
from cache import _path_key_builder, invalidate_cache
from fastapi_cache.decorator import cache
from i18n import t
from rate_limit import limiter
from documents_router import (
    _access_params,
    _access_where_clause,
    _build_field_set_clause,
    _delete_links_for_document,
    _document_visible,
    _extra_cols,
    _get_tables_columns,
    _sanitize,
)
from ingest_router import _engine, _retry_document, _grant_uploader_access

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

def _queue_select(table: str, cols: dict[str, str], current_user: CurrentUser) -> str:
    """Build one UNION branch for review_required rows in `table`. Same
    fixed-shape/extra_fields approach as documents_router._per_table_select
    — see that function's docstring for why."""
    extra = _extra_cols(cols)
    extra_expr = (
        "jsonb_build_object(" + ", ".join(f"'{c}', {c}" for c in extra) + ")"
        if extra else "'{}'::jsonb"
    )
    sel = [
        "id::text", f"'{table}'::text AS table_name",
        "document_type::text AS document_type" if "document_type" in cols else "NULL::text AS document_type",
        "record_id::text AS record_id" if "record_id" in cols else "NULL::text AS record_id",
        "confidence::text     AS confidence",
        "review_status::text  AS review_status",
        "ingested_at::text    AS ingested_at",
        "source_image_path",
        f"{extra_expr} AS extra_fields",
    ]
    where = "review_status = 'review_required'"
    access_clause = _access_where_clause(table, current_user)
    if access_clause:
        where += f" AND {access_clause}"
    return (
        f"SELECT {', '.join(sel)}\n"
        f"FROM \"{table}\"\n"
        f"WHERE {where}"
    )


async def _run_queue(
    conn,
    tables_cols: dict,
    *,
    only_table: Optional[str],
    page: int,
    page_size: int,
    current_user: CurrentUser,
) -> tuple[list[dict], int]:
    scope = (
        {only_table: tables_cols[only_table]}
        if only_table and only_table in tables_cols
        else tables_cols
    )
    if not scope:
        return [], 0

    parts = [_queue_select(tbl, cols, current_user) for tbl, cols in scope.items()]
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
        {"page_size": page_size, "offset": (page - 1) * page_size, **_access_params(current_user)},
    )
    rows  = result.mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0
    return [
        {k: v for k, v in dict(r).items() if k != "total_count"}
        for r in rows
    ], total


async def _pending_count(conn, tables_cols: dict, current_user: CurrentUser) -> int:
    if not tables_cols:
        return 0
    parts = []
    for t in tables_cols:
        where = "review_status = 'review_required'"
        access_clause = _access_where_clause(t, current_user)
        if access_clause:
            where += f" AND {access_clause}"
        parts.append(f'SELECT COUNT(*) AS n FROM "{t}" WHERE {where}')
    union  = "\nUNION ALL\n".join(parts)
    result = await conn.execute(
        text(f"SELECT COALESCE(SUM(n), 0) FROM ({union}) sub"),
        _access_params(current_user),
    )
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
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        return {"pending": await _pending_count(conn, tables_cols, current_user)}


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
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if only_table and only_table not in tables_cols:
            return {"total": 0, "items": []}

        items, total = await _run_queue(
            conn, tables_cols,
            only_table=only_table,
            page=page,
            page_size=page_size,
            current_user=current_user,
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
        raise HTTPException(status_code=422, detail=t("review.invalid_action", current_user.locale))

    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

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
            editable, set_parts, field_params = _build_field_set_clause(cols, body.fields)
            params = {"doc_id": doc_id, **field_params}

            # Approving with no actual field changes is just a status
            # transition, open to any reviewer; submitting corrected
            # values is editing extraction content, which needs the
            # delegated permission (admin, or can_edit_extraction).
            if editable and not current_user.can_edit_extraction_data:
                raise HTTPException(
                    status_code=403, detail=t("auth.extraction_editor_required", current_user.locale)
                )

            set_parts += [
                "review_status = 'approved'",
                "reviewed_at   = NOW()",
                "reviewed_by   = CAST(:reviewed_by_id AS uuid)",
            ]
            params["reviewed_by_id"] = current_user.id

            sql = (
                f'UPDATE "{safe}" SET {", ".join(set_parts)} '
                f"WHERE id = CAST(:doc_id AS uuid) RETURNING *"
            )

        else:  # reject
            sql    = (
                f"UPDATE \"{safe}\""
                f" SET review_status = 'rejected',"
                f"     reviewed_by   = CAST(:reviewed_by_id AS uuid)"
                f" WHERE id = CAST(:doc_id AS uuid) RETURNING *"
            )
            params = {"doc_id": doc_id, "reviewed_by_id": current_user.id}

        result = await conn.execute(text(sql), params)
        await conn.commit()

    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    # This document just left the shared review pipeline (approved or
    # rejected) — it stops being open to every tenant user by default from
    # here on. See _grant_uploader_access's docstring.
    async with _engine().begin() as grant_conn:
        await _grant_uploader_access(
            grant_conn, current_user.tenant_id, safe, doc_id, row.get("uploaded_by") and str(row["uploaded_by"]),
        )

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
    # Both approve and reject set reviewed_by — invalidate the reviewer
    # filter list too, since this may be the first review on this table
    # (the reviewed_by column, and this user's entry in the list, are new).
    await invalidate_cache("review", "reviewers")

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
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        result = await conn.execute(
            text(
                f"UPDATE \"{safe}\" SET review_status = 'manual_entry' "
                f"WHERE id = CAST(:doc_id AS uuid) RETURNING id"
            ),
            {"doc_id": doc_id},
        )
        await conn.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    await log_action(
        action="document_flagged",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")


@router.post("/{table_name}/{doc_id}/retry")
@limiter.limit("20/minute")
async def retry_document(
    request:      Request,
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Re-run VLM extraction on a crashed document, reusing its already
    preprocessed image(s) — no re-upload needed. Only valid for documents
    in manual_entry (crashed) status; the old stub row is replaced."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    batch_id = await _retry_document(safe, doc_id, current_user.tenant_id, current_user.tenant_slug, current_user.locale)

    await log_action(
        action="document_retried",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"new_batch_id": batch_id},
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")

    return {"batch_id": batch_id}


@router.delete("/{table_name}/{doc_id}")
@limiter.limit("30/minute")
async def delete_document(
    request:      Request,
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(require_admin),
):
    """Permanently remove a document. Admin only — this is irreversible,
    unlike reject/flag which just change review_status. Also removes any
    chain-of-custody links referencing this document, in the same
    transaction, so nothing is left pointing at a gone row."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))

        result = await conn.execute(
            text(f'DELETE FROM "{safe}" WHERE id = CAST(:doc_id AS uuid) RETURNING id'),
            {"doc_id": doc_id},
        )
        if result.rowcount > 0:
            await _delete_links_for_document(conn, current_user.tenant_id, safe, doc_id)
        await conn.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    await log_action(
        action="document_deleted",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")

    return {"ok": True}
