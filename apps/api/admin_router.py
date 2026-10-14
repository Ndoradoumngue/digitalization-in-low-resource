"""
Admin-only endpoints.

Endpoints:
  GET    /api/admin/audit-log            - paginated audit trail
  GET    /api/admin/groups               - list this tenant's groups
  POST   /api/admin/groups               - create a group
  DELETE /api/admin/groups/{group_id}    - delete a group
  (accounts themselves: users_router.py)
  POST   /api/admin/users/{user_id}/groups              - add a user to a group
  DELETE /api/admin/users/{user_id}/groups/{group_id}   - remove a user from a group
  POST   /api/admin/integrity-check      - on-demand fixity check for this tenant (+ audit chain)
  GET    /api/admin/integrity            - last completed integrity check of this tenant
  GET    /api/admin/integrity/audit-chain - verify the audit log's hash chain now
  GET    /api/admin/export               - full archive export (data + source files) as a zip

All admin role required - group/membership management stays admin-only
even though *tagging a document* with an existing group/person (see
documents_router's /access endpoints) can be delegated via can_manage_access.
"""

import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from audit import display_email_sql, log_action
from auth import CurrentUser, require_admin
from documents_router import _SAFE_FILE_ROOTS, _get_tables_columns
from export_format import insert_statement, serial
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from i18n import t
from ingest_router import (
    _audit_chain_check,
    _chain_summary,
    _engine,
    _record_integrity_run,
    _run_integrity_check,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.background import BackgroundTask

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/audit-log", summary="Audit log")
async def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user_id: str | None = None,
    action: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: CurrentUser = Depends(require_admin),
):
    """
    Return a paginated, filterable list of this tenant's audit log
    entries, newest first. Requires admin role.

    Query parameters:
      page / page_size  - pagination
      user_id           - filter by user UUID
      action            - exact action name (e.g. 'document_approved')
      date_from / date_to - ISO-8601 timestamps (inclusive)
    """
    # The log is one table for the whole installation - an admin only sees
    # entries about their own tenant: actions by its users, or on its
    # documents (every per-type table name starts with the tenant prefix).
    where: list[str] = [
        "(user_id IN (SELECT id FROM sdai_users WHERE tenant_id = CAST(:tid AS uuid))"
        " OR starts_with(table_name, :table_prefix))"
    ]
    params: dict = {
        "page_size": page_size,
        "offset": (page - 1) * page_size,
        "tid": current_user.tenant_id,
        "table_prefix": f"t_{current_user.tenant_slug}_",
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
            id, user_id,
            {display_email_sql()} AS user_email,
            action, table_name,
            document_id, details, ip_address, created_at,
            COUNT(*) OVER() AS total_count
        FROM sdai_audit_log
        {where_sql}
        ORDER BY created_at DESC
        LIMIT :page_size OFFSET :offset
    """

    async with _engine().connect() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.mappings().all()

    total = int(rows[0]["total_count"]) if rows else 0
    items = [{k: serial(v) for k, v in dict(r).items() if k != "total_count"} for r in rows]

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
            {
                "id": str(r[0]),
                "name": r[1],
                "created_at": r[2].isoformat(),
                "member_count": int(r[3]),
            }
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
            raise HTTPException(
                status_code=409,
                detail=t("common.group_already_exists", current_user.locale, name=body.name),
            )
    return {"id": str(group_id), "name": body.name}


@router.delete("/groups/{group_id}", summary="Delete a group")
async def delete_group(group_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Deleting a group also removes its memberships and any document
    access grants that named it (ON DELETE CASCADE on sdai_user_groups;
    sdai_document_access has no FK to sdai_groups since grantee_id is
    polymorphic, so those rows are cleaned up explicitly below)."""
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM sdai_groups WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) RETURNING id"
            ),
            {"id": group_id, "tid": current_user.tenant_id},
        )
        if result.one_or_none() is None:
            raise HTTPException(
                status_code=404, detail=t("common.group_not_found", current_user.locale)
            )
        await conn.execute(
            text(
                "DELETE FROM sdai_document_access WHERE grantee_type = 'group' AND grantee_id = CAST(:id AS uuid)"
            ),
            {"id": group_id},
        )
    return {"ok": True}


# ── Users ─────────────────────────────────────────────────────────────────────


class GroupMembershipBody(BaseModel):
    group_id: str


@router.post("/users/{user_id}/groups", summary="Add a user to a group")
async def add_user_to_group(
    user_id: str, body: GroupMembershipBody, current_user: CurrentUser = Depends(require_admin)
):
    async with _engine().begin() as conn:
        # Both rows must belong to this admin's own tenant.
        user_row = await conn.execute(
            text(
                "SELECT 1 FROM sdai_users WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                " AND deleted_at IS NULL"
            ),
            {"id": user_id, "tid": current_user.tenant_id},
        )
        if user_row.one_or_none() is None:
            raise HTTPException(
                status_code=404, detail=t("common.user_not_found", current_user.locale)
            )
        group_row = await conn.execute(
            text(
                "SELECT 1 FROM sdai_groups WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
            ),
            {"id": body.group_id, "tid": current_user.tenant_id},
        )
        if group_row.one_or_none() is None:
            raise HTTPException(
                status_code=404, detail=t("common.group_not_found", current_user.locale)
            )

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
async def remove_user_from_group(
    user_id: str, group_id: str, current_user: CurrentUser = Depends(require_admin)
):
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
    ingest_router) - re-hashes/verifies every document's referenced file(s)
    for this tenant right now and returns a summary. Each failure is also
    logged as an integrity_check_failed audit entry, same as the
    background scan, so results are visible in the audit log afterward too.
    Also verifies the audit log's hash chain (see /integrity/audit-chain)."""
    counts = await _run_integrity_check(current_user.tenant_slug)
    chain = await _audit_chain_check()
    await _record_integrity_run(
        current_user.tenant_slug, counts, chain, current_user.id, current_user.email
    )
    return {**counts, "audit_chain": _chain_summary(chain)}


@router.get("/integrity", summary="Last integrity check of this tenant")
async def last_integrity_check(current_user: CurrentUser = Depends(require_admin)):
    """The most recent completed check (background or on demand) - its
    fixity counts and audit-chain verdict - or null when none ran yet."""
    async with _engine().connect() as conn:
        row = (
            await conn.execute(
                text(f"""
                    SELECT created_at, {display_email_sql()} AS user_email, details FROM sdai_audit_log
                    WHERE action = 'integrity_check_completed' AND details->>'tenant' = :slug
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"slug": current_user.tenant_slug},
            )
        ).mappings().one_or_none()
    if row is None:
        return {"last_run": None}
    details = row["details"] or {}
    return {
        "last_run": {
            "ran_at": serial(row["created_at"]),
            "ran_by": row["user_email"],
            "fixity": details.get("fixity"),
            "audit_chain": details.get("audit_chain"),
        }
    }


@router.get("/integrity/audit-chain", summary="Verify the audit log's hash chain")
async def verify_audit_chain(current_user: CurrentUser = Depends(require_admin)):
    """Re-verify every node's audit chain right now: returns ok, how many
    entries were checked, each chain's length and head, and the first broken
    entry (its position only - never another tenant's content). A break is
    also logged as integrity_chain_broken."""
    return await _audit_chain_check()


# ── Archive export ────────────────────────────────────────────────────────────


def _document_files(row: dict) -> set[str]:
    """Every source file a document row references - the primary image,
    the original PDF (if any), and every page image for a multi-page
    document. A set: multi-page documents whose single page is also the
    primary image would otherwise get that file twice."""
    paths: set[str] = set()
    if row.get("source_image_path"):
        paths.add(row["source_image_path"])
    if row.get("source_pdf_path"):
        paths.add(row["source_pdf_path"])
    for p in row.get("page_image_paths") or []:
        paths.add(p)
    return paths


@router.get("/export", summary="Export the tenant's archive (data + source files)")
async def export_archive(
    format: str = Query("json", pattern="^(json|sql)$"),
    current_user: CurrentUser = Depends(require_admin),
):
    """Every document row in this tenant, in the requested format, bundled
    into a zip alongside a documents/ folder containing every referenced
    source file (page images, original PDFs) - a full, portable backup of
    the tenant's archive, not just its metadata. Admin-only: this bypasses
    per-document access grants entirely (a full-archive export is a
    structural/bulk action, same tier as integrity checks and schema
    changes, not something to scope down to "whatever the requester can
    currently see")."""
    tenant_slug = current_user.tenant_slug

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    exported_at = datetime.now(timezone.utc)
    total_rows = 0

    try:
        async with _engine().connect() as conn:
            tables_cols = await _get_tables_columns(conn, tenant_slug)

            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                json_tables: dict = {}
                sql_lines: list[str] = [
                    f"-- SDAI archive export - tenant '{tenant_slug}' - {exported_at.isoformat()}",
                ]

                for table_name, cols in sorted(tables_cols.items()):
                    result = await conn.execute(text(f'SELECT * FROM "{table_name}"'))
                    rows = [dict(r) for r in result.mappings().all()]
                    total_rows += len(rows)

                    if format == "json":
                        json_tables[table_name] = {
                            "columns": cols,
                            "rows": [{k: serial(v) for k, v in row.items()} for row in rows],
                        }
                    else:
                        col_defs = ", ".join(f'"{c}" {t}' for c, t in cols.items())
                        sql_lines.append(
                            f'\nCREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs});'
                        )
                        for row in rows:
                            sql_lines.append(insert_statement(table_name, row))

                    for row in rows:
                        doc_key = row.get("record_id") or str(row.get("id"))
                        for p in _document_files(row):
                            file_path = Path(p).resolve()
                            if not any(file_path.is_relative_to(root) for root in _SAFE_FILE_ROOTS):
                                continue
                            if not file_path.exists():
                                continue
                            zf.write(
                                file_path, f"documents/{table_name}/{doc_key}/{file_path.name}"
                            )

                if format == "json":
                    export_doc = {
                        "exported_at": exported_at.isoformat(),
                        "tenant": tenant_slug,
                        "tables": json_tables,
                    }
                    zf.writestr("export.json", json.dumps(export_doc, ensure_ascii=False, indent=2))
                else:
                    zf.writestr("export.sql", "\n".join(sql_lines) + "\n")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    await log_action(
        action="archive_exported",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"format": format, "row_count": total_rows},
    )

    filename = f"archive_export_{tenant_slug}_{format}_{exported_at.strftime('%Y%m%d_%H%M%S')}.zip"
    return FileResponse(
        tmp_path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )
