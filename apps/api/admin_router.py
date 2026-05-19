"""
Admin-only endpoints.

Endpoints:
  GET /api/admin/audit-log  — paginated audit trail (admin role required)
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from auth import CurrentUser, require_admin
from ingest_router import _engine

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _serial(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "hex"):           # UUID object from asyncpg
        return str(v)
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


@router.get("/audit-log", summary="Audit log")
async def get_audit_log(
    page:      int           = Query(1,  ge=1),
    page_size: int           = Query(50, ge=1, le=200),
    user_id:   Optional[str] = None,
    action:    Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    _:         CurrentUser   = Depends(require_admin),
):
    """
    Return a paginated, filterable list of audit log entries, newest first.
    Requires admin role.

    Query parameters:
      page / page_size  — pagination
      user_id           — filter by user UUID
      action            — exact action name (e.g. 'document_approved')
      date_from / date_to — ISO-8601 timestamps (inclusive)
    """
    where: list[str] = []
    params: dict = {
        "page_size": page_size,
        "offset":    (page - 1) * page_size,
    }

    if user_id:
        where.append("user_id = :user_id::uuid")
        params["user_id"] = user_id
    if action:
        where.append("action = :action")
        params["action"] = action
    if date_from:
        where.append("created_at >= :date_from::timestamptz")
        params["date_from"] = date_from
    if date_to:
        where.append("created_at <= :date_to::timestamptz")
        params["date_to"] = date_to

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT
            id, user_id, user_email, action, table_name,
            document_id, details, ip_address, created_at,
            COUNT(*) OVER() AS total_count
        FROM sdai_audit_log
        {where_sql}
        ORDER BY created_at DESC
        LIMIT :page_size OFFSET :offset
    """

    async with _engine().connect() as conn:
        result = await conn.execute(text(sql), params)
        rows   = result.mappings().all()

    total = int(rows[0]["total_count"]) if rows else 0
    items = [
        {k: _serial(v) for k, v in dict(r).items() if k != "total_count"}
        for r in rows
    ]

    return {"total": total, "page": page, "page_size": page_size, "items": items}
