"""
Admin-only endpoints.

Endpoints:
  GET    /api/admin/audit-log            — paginated audit trail
  GET    /api/admin/groups               — list this tenant's groups
  POST   /api/admin/groups               — create a group
  DELETE /api/admin/groups/{group_id}    — delete a group
  GET    /api/admin/users                — list this tenant's users, with group membership
  PATCH  /api/admin/users/{user_id}      — toggle can_manage_access
  POST   /api/admin/users/{user_id}/groups              — add a user to a group
  DELETE /api/admin/users/{user_id}/groups/{group_id}   — remove a user from a group
  POST   /api/admin/integrity-check      — on-demand fixity check for this tenant

All admin role required — group/membership management stays admin-only
even though *tagging a document* with an existing group/person (see
documents_router's /access endpoints) can be delegated via can_manage_access.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from auth import CurrentUser, require_admin
from ingest_router import _engine, _run_integrity_check

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
        where.append("user_id = CAST(:user_id AS uuid)")
        params["user_id"] = user_id
    if action:
        where.append("action = :action")
        params["action"] = action
    if date_from:
        where.append("created_at >= CAST(:date_from AS timestamptz)")
        params["date_from"] = date_from
    if date_to:
        where.append("created_at <= CAST(:date_to AS timestamptz)")
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


# ── Groups ────────────────────────────────────────────────────────────────────

class CreateGroupBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


@router.get("/groups", summary="List groups")
async def list_groups(current_user: CurrentUser = Depends(require_admin)):
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT g.id, g.name, g.created_at, COUNT(ug.user_id) AS member_count
                FROM sdai_groups g
                LEFT JOIN sdai_user_groups ug ON ug.group_id = g.id
                WHERE g.tenant_id = CAST(:tid AS uuid)
                GROUP BY g.id, g.name, g.created_at
                ORDER BY g.name
            """),
            {"tid": current_user.tenant_id},
        )
        return [
            {"id": str(r[0]), "name": r[1], "created_at": r[2].isoformat(), "member_count": int(r[3])}
            for r in rows
        ]


@router.post("/groups", summary="Create a group")
async def create_group(body: CreateGroupBody, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO sdai_groups (tenant_id, name, created_by)
                VALUES (CAST(:tid AS uuid), :name, CAST(:cb AS uuid))
                ON CONFLICT (tenant_id, name) DO NOTHING
                RETURNING id
            """),
            {"tid": current_user.tenant_id, "name": body.name, "cb": current_user.id},
        )
        group_id = result.scalar()
        if group_id is None:
            raise HTTPException(status_code=409, detail=f"Group '{body.name}' already exists")
    return {"id": str(group_id), "name": body.name}


@router.delete("/groups/{group_id}", summary="Delete a group")
async def delete_group(group_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Deleting a group also removes its memberships and any document
    access grants that named it (ON DELETE CASCADE on sdai_user_groups;
    sdai_document_access has no FK to sdai_groups since grantee_id is
    polymorphic, so those rows are cleaned up explicitly below)."""
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("DELETE FROM sdai_groups WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) RETURNING id"),
            {"id": group_id, "tid": current_user.tenant_id},
        )
        if result.one_or_none() is None:
            raise HTTPException(status_code=404, detail="Group not found")
        await conn.execute(
            text("DELETE FROM sdai_document_access WHERE grantee_type = 'group' AND grantee_id = CAST(:id AS uuid)"),
            {"id": group_id},
        )
    return {"ok": True}


# ── Users ─────────────────────────────────────────────────────────────────────

class UpdateUserBody(BaseModel):
    can_manage_access: bool


class GroupMembershipBody(BaseModel):
    group_id: str


@router.get("/users", summary="List users")
async def list_users(current_user: CurrentUser = Depends(require_admin)):
    """Tenant's users with role, can_manage_access, and current group
    membership — powers the group-membership picker and the per-document
    individual-user access grant picker."""
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT u.id, u.email, u.full_name, u.role, u.can_manage_access,
                       COALESCE(ARRAY_AGG(ug.group_id) FILTER (WHERE ug.group_id IS NOT NULL), '{}') AS group_ids
                FROM sdai_users u
                LEFT JOIN sdai_user_groups ug ON ug.user_id = u.id
                WHERE u.tenant_id = CAST(:tid AS uuid)
                GROUP BY u.id, u.email, u.full_name, u.role, u.can_manage_access
                ORDER BY u.email
            """),
            {"tid": current_user.tenant_id},
        )
        return [
            {
                "id": str(r[0]), "email": r[1], "full_name": r[2], "role": r[3],
                "can_manage_access": r[4], "group_ids": [str(g) for g in r[5]],
            }
            for r in rows
        ]


@router.patch("/users/{user_id}", summary="Update a user's access-management permission")
async def update_user(user_id: str, body: UpdateUserBody, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("""
                UPDATE sdai_users SET can_manage_access = :cma
                WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
                RETURNING id
            """),
            {"cma": body.can_manage_access, "id": user_id, "tid": current_user.tenant_id},
        )
        if result.one_or_none() is None:
            raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.post("/users/{user_id}/groups", summary="Add a user to a group")
async def add_user_to_group(user_id: str, body: GroupMembershipBody, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        # Both rows must belong to this admin's own tenant.
        user_row = await conn.execute(
            text("SELECT 1 FROM sdai_users WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"id": user_id, "tid": current_user.tenant_id},
        )
        if user_row.one_or_none() is None:
            raise HTTPException(status_code=404, detail="User not found")
        group_row = await conn.execute(
            text("SELECT 1 FROM sdai_groups WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"id": body.group_id, "tid": current_user.tenant_id},
        )
        if group_row.one_or_none() is None:
            raise HTTPException(status_code=404, detail="Group not found")

        await conn.execute(
            text("""
                INSERT INTO sdai_user_groups (user_id, group_id)
                VALUES (CAST(:uid AS uuid), CAST(:gid AS uuid))
                ON CONFLICT DO NOTHING
            """),
            {"uid": user_id, "gid": body.group_id},
        )
    return {"ok": True}


@router.delete("/users/{user_id}/groups/{group_id}", summary="Remove a user from a group")
async def remove_user_from_group(user_id: str, group_id: str, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        await conn.execute(
            text("""
                DELETE FROM sdai_user_groups
                WHERE user_id = CAST(:uid AS uuid) AND group_id = CAST(:gid AS uuid)
                  AND user_id IN (SELECT id FROM sdai_users WHERE tenant_id = CAST(:tid AS uuid))
            """),
            {"uid": user_id, "gid": group_id, "tid": current_user.tenant_id},
        )
    return {"ok": True}


# ── Fixity / integrity verification ─────────────────────────────────────────

@router.post("/integrity-check", summary="Run a fixity check for this tenant")
async def run_integrity_check(current_user: CurrentUser = Depends(require_admin)):
    """On-demand version of the background scan (_integrity_check_worker in
    ingest_router) — re-hashes/verifies every document's referenced file(s)
    for this tenant right now and returns a summary. Each failure is also
    logged as an integrity_check_failed audit entry, same as the
    background scan, so results are visible in the audit log afterward too."""
    return await _run_integrity_check(current_user.tenant_slug)
