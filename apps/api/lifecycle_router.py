"""Lifecycle routes (GED Phase 6) - archive, trash, restore. Every change
goes through lifecycle.py (the state machine); this module only checks who
may ask, translates refusals and writes the audit trail.

  GET    /api/db/documents/{t}/{id}/lifecycle   status, retention, holds, trash info and the
                                                actions the caller may take (read)
  POST   /api/db/documents/{t}/{id}/archive     active → archived (lifecycle manager)
  POST   /api/db/documents/{t}/{id}/trash       move to the trash {reason?} (lifecycle manager)
  POST   /api/db/documents/{t}/{id}/restore     back from the trash (lifecycle manager)
  GET    /api/db/trash                          the trash: documents the caller could read,
                                                purge date, hold (lifecycle manager)
  POST   /api/db/documents/{t}/{id}/retention/extend   eligible → archived, kept until {until}
  GET|POST /api/db/retention-rules, PATCH|DELETE /api/db/retention-rules/{id}   rules
                                                (lifecycle manager; every change recomputes)
  POST   /api/db/retention/run                  run the daily job now (admin, local node)
  GET|POST /api/db/legal-holds, POST /api/db/legal-holds/{id}/release   holds on a document,
                                                a series (recursive) or a search result
  GET    /api/db/destruction/eligible           documents whose retention ended
  GET|POST /api/db/destructions, GET /api/db/destructions/{id}   proposals / records
  POST   /api/db/destructions/{id}/approve|reject   admin, never the proposer
  POST   /api/db/destructions/{id}/cancel       the proposer withdraws it
  GET    /api/db/destructions/{id}/pv           the procès-verbal (PDF)

A "lifecycle manager" is an admin or a reviewer delegated can_manage_lifecycle
(CurrentUser.can_manage_document_lifecycle). Archive and trash also need the
edit level on the document; restore needs read on the trashed row.

Upstream's hard deletes (review_router.delete_document, the ingest batch /
document deletes) now trash through trash_document below - nothing is
removed without a trace; the only way out of the database is destruction
(destruction.py).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

import destruction
import lifecycle
import notifications
import retention
from audit import client_ip, log_action
from auth import CurrentUser, require_admin, require_reviewer, require_user
from cache import invalidate_cache
from documents_router import (
    _ACCESS_CACHE_NAMESPACES,
    _access_params,
    _access_where_clause,
    _display_col,
    _get_tables_columns,
    _require_document_permission,
    _run_list_query,
    _sanitize,
)
from export_router import DocumentRef, SearchSelection
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from i18n import t
from ingest_router import _engine
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

router = APIRouter(prefix="/api/db", tags=["lifecycle"])


def lifecycle_http(exc: lifecycle.LifecycleError, locale: str) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail=t(exc.key, locale, **exc.params)
    )


def require_lifecycle_manager(current_user: CurrentUser) -> None:
    if not current_user.can_manage_document_lifecycle:
        raise HTTPException(
            status_code=403, detail=t("lifecycle.manager_required", current_user.locale)
        )


async def _table(conn, current_user: CurrentUser, table_name: str) -> tuple[str, dict]:
    safe = _sanitize(table_name)
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    if safe not in tables_cols:
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe, tables_cols[safe]


async def _require_trashed_readable(
    conn, table: str, doc_id: str, current_user: CurrentUser
):
    """404 unless the caller could read the document if it were live."""
    clause = _access_where_clause(table, current_user, include_deleted=True)
    where = "id = CAST(:id AS uuid)" + (f" AND {clause}" if clause else "")
    row = (
        await conn.execute(
            text(f'SELECT 1 FROM "{table}" WHERE {where}'),
            {"id": doc_id, **_access_params(current_user)},
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=t("common.document_not_found", current_user.locale)
        )


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _after_change() -> None:
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)


# ── Trash (also used by the replaced hard-delete routes) ──────────────────────


async def trash_document(
    request: Request | None,
    table: str,
    doc_id: str,
    current_user: CurrentUser,
    reason: str | None = None,
    via: str = "trash",
) -> None:
    """Check edit permission, move to the trash, audit. Raises HTTPException."""
    async with _engine().begin() as conn:
        await _require_document_permission(conn, table, doc_id, current_user, "edit")
        try:
            row = await lifecycle.trash(conn, table, doc_id, current_user.id, reason)
        except lifecycle.LifecycleError as exc:
            raise lifecycle_http(exc, current_user.locale) from None
    await log_action(
        action="document_trashed",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=table,
        document_id=doc_id,
        details={"reason": reason, "via": via, "record_id": row["record_id"]},
        ip_address=client_ip(request) if request is not None else None,
    )
    await _after_change()


class TrashBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post(
    "/documents/{table_name}/{doc_id}/trash", summary="Move a document to the trash"
)
async def trash_route(
    request: Request,
    table_name: str,
    doc_id: str,
    body: TrashBody | None = None,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        safe, _ = await _table(conn, current_user, table_name)
    await trash_document(
        request, safe, doc_id, current_user, body.reason if body else None
    )
    return {"ok": True}


@router.post(
    "/documents/{table_name}/{doc_id}/restore", summary="Restore from the trash"
)
async def restore_route(
    request: Request,
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        safe, _ = await _table(conn, current_user, table_name)
        await _require_trashed_readable(conn, safe, doc_id, current_user)
        try:
            row = await lifecycle.restore(conn, safe, doc_id, current_user.tenant_slug)
        except lifecycle.LifecycleError as exc:
            raise lifecycle_http(exc, current_user.locale) from None
    await log_action(
        action="document_restored",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"record_id": row["record_id"], "trashed_at": _iso(row["deleted_at"])},
        ip_address=client_ip(request),
    )
    await _after_change()
    return {"ok": True}


@router.get("/trash", summary="Documents in the trash")
async def list_trash(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Trashed (not yet purged) documents the caller could read, newest
    first, with the date the daily job will purge them."""
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        parts = []
        for table, cols in sorted(tables_cols.items()):
            display = _display_col(cols)
            access = _access_where_clause(table, current_user, include_deleted=True)
            parts.append(f"""
                SELECT '{table}' AS table_name, id::text AS id, record_id,
                       {"document_type::text" if "document_type" in cols else "NULL::text"} AS document_type,
                       {f'"{display}"::text' if display else "NULL::text"} AS display,
                       deleted_at, deleted_by, deleted_reason,
                       COALESCE(lifecycle_status, 'active') AS lifecycle_status,
                       {lifecycle.held_sql(table)} AS held
                FROM "{table}"
                WHERE deleted_at IS NOT NULL AND lifecycle_status IS DISTINCT FROM 'destroyed'
                {f"AND {access}" if access else ""}
            """)
        if not parts:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "retention_days": lifecycle.TRASH_RETENTION_DAYS,
            }
        union = " UNION ALL ".join(parts)
        params = {
            **_access_params(current_user),
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        total = (
            await conn.execute(text(f"SELECT count(*) FROM ({union}) x"), params)
        ).scalar()
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT x.*, u.email AS deleted_by_email FROM ({union}) x
                    LEFT JOIN sdai_users u ON u.id = x.deleted_by
                    ORDER BY x.deleted_at DESC, x.id LIMIT :limit OFFSET :offset
                """),
                    params,
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": [
            {
                "table_name": r["table_name"],
                "id": r["id"],
                "record_id": r["record_id"],
                "document_type": r["document_type"],
                "display": r["display"],
                "lifecycle_status": r["lifecycle_status"],
                "deleted_at": _iso(r["deleted_at"]),
                "deleted_by": r["deleted_by_email"],
                "deleted_reason": r["deleted_reason"],
                "purge_at": _iso(lifecycle.purge_at(r["deleted_at"])),
                "restorable": not lifecycle.purge_due(r["deleted_at"]),
                "legal_hold": bool(r["held"]),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "retention_days": lifecycle.TRASH_RETENTION_DAYS,
    }


# ── Archive ───────────────────────────────────────────────────────────────────


@router.post("/documents/{table_name}/{doc_id}/archive", summary="Archive a document")
async def archive_route(
    request: Request,
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        safe, _ = await _table(conn, current_user, table_name)
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        try:
            row = await lifecycle.archive(conn, safe, doc_id, current_user.id)
        except lifecycle.LifecycleError as exc:
            raise lifecycle_http(exc, current_user.locale) from None
        await after_archive(conn, current_user.tenant_id, safe, doc_id)
    await log_action(
        action="document_archived",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"record_id": row["record_id"]},
        ip_address=client_ip(request),
    )
    await _after_change()
    return {"ok": True}


async def after_archive(conn, tenant_id: str, table: str, doc_id: str) -> None:
    """Hook run in the archive transaction (retention dates that run from
    the archiving date - filled in by the retention module)."""
    await retention.recompute(conn, tenant_id, tables=[table], doc_ids=[doc_id])


# ── Lifecycle panel ───────────────────────────────────────────────────────────


@router.get(
    "/documents/{table_name}/{doc_id}/lifecycle", summary="Lifecycle of a document"
)
async def get_lifecycle(
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Status, retention date and rule, legal holds, trash info and the
    lifecycle actions the caller may take on this document."""
    async with _engine().connect() as conn:
        safe, _ = await _table(conn, current_user, table_name)
        if current_user.can_manage_document_lifecycle:
            await _require_trashed_readable(conn, safe, doc_id, current_user)
        else:
            await _require_document_permission(conn, safe, doc_id, current_user, "read")
        row = await lifecycle.load(conn, safe, doc_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=t("common.document_not_found", current_user.locale),
            )
        rule = None
        if row["retention_rule_id"]:
            rule = (
                (
                    await conn.execute(
                        text("""
                        SELECT id::text AS id, name, scope, duration_months, trigger_on,
                               final_action
                        FROM sdai_retention_rules WHERE id = CAST(:id AS uuid)
                    """),
                        {"id": row["retention_rule_id"]},
                    )
                )
                .mappings()
                .one_or_none()
            )
        holds = (
            (
                await conn.execute(
                    text(f"""
                    SELECT lh.id::text AS id, lh.scope, lh.origin, lh.reason, lh.created_at,
                           u.email AS created_by, s.name AS series_name
                    FROM sdai_legal_holds lh
                    LEFT JOIN sdai_users u ON u.id = lh.created_by
                    LEFT JOIN sdai_series s ON s.id = lh.series_id
                    WHERE lh.released_at IS NULL AND lh.tenant_id = CAST(:tid AS uuid) AND (
                        (lh.scope = 'documents' AND EXISTS (
                            SELECT 1 FROM sdai_legal_hold_documents lhd
                            WHERE lhd.hold_id = lh.id AND lhd.table_name = :t
                              AND lhd.document_id = CAST(:id AS uuid)))
                        OR (lh.scope = 'series' AND EXISTS (
                            SELECT 1 FROM "{safe}" d JOIN sdai_series hs ON hs.id = d.series_id
                            WHERE d.id = CAST(:id AS uuid)
                              AND (hs.id = lh.series_id
                                   OR strpos(hs.path, '/' || lh.series_id::text || '/') > 0))))
                    ORDER BY lh.created_at
                """),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
        can_edit = await _can(conn, safe, doc_id, current_user, "edit")
    status = row["lifecycle_status"]
    trashed = row["deleted_at"] is not None
    manager = current_user.can_manage_document_lifecycle
    actions = []
    if manager and status != "destroyed":
        if trashed:
            if not lifecycle.purge_due(row["deleted_at"]):
                actions.append("restore")
        else:
            if can_edit and lifecycle.transition_op(status, "archived") == "archive":
                actions.append("archive")
            if can_edit and not row["held"]:
                actions.append("trash")
            if status == "destruction_eligible":
                actions.append("extend")
            actions.append("hold")
            if holds:
                actions.append("release_hold")
    return {
        "lifecycle_status": status,
        "archived_at": _iso(row["archived_at"]),
        "retention_until": _iso(row["retention_until"]),
        "retention_override": _iso(row["retention_override"]),
        "retention_rule": dict(rule) if rule else None,
        "legal_hold": bool(row["held"]),
        "holds": [{**dict(h), "created_at": _iso(h["created_at"])} for h in holds],
        "in_trash": trashed,
        "deleted_at": _iso(row["deleted_at"]),
        "deleted_reason": row["deleted_reason"],
        "purge_at": _iso(lifecycle.purge_at(row["deleted_at"])),
        "actions": actions,
    }


async def _can(
    conn, table: str, doc_id: str, current_user: CurrentUser, permission: str
) -> bool:
    clause = _access_where_clause(table, current_user, permission)
    where = "id = CAST(:id AS uuid)" + (f" AND {clause}" if clause else "")
    return (
        await conn.execute(
            text(f'SELECT 1 FROM "{table}" WHERE {where}'),
            {"id": doc_id, **_access_params(current_user)},
        )
    ).one_or_none() is not None


class ExtendBody(BaseModel):
    until: date
    reason: str | None = Field(default=None, max_length=500)


@router.post("/documents/{table_name}/{doc_id}/retention/extend", summary="Keep longer")
async def extend_route(
    request: Request,
    table_name: str,
    doc_id: str,
    body: ExtendBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """destruction_eligible → archived again, kept until `until` (a manual
    date that wins over the rules) - the outcome of a "review"."""
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        safe, _ = await _table(conn, current_user, table_name)
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        try:
            row = await lifecycle.extend(conn, safe, doc_id, body.until)
        except lifecycle.LifecycleError as exc:
            raise lifecycle_http(exc, current_user.locale) from None
    await log_action(
        action="retention_extended",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "record_id": row["record_id"],
            "until": body.until.isoformat(),
            "previous": _iso(row["retention_until"]),
            "reason": body.reason,
        },
        ip_address=client_ip(request),
    )
    await _after_change()
    return {"ok": True}


# ── Retention rules ───────────────────────────────────────────────────────────


_RULE_COLS = """
    r.id::text AS id, r.name, r.scope, r.series_id::text AS series_id, r.table_name,
    r.duration_months, r.trigger_on, r.date_field, r.final_action, r.created_at,
    s.name AS series_name, s.code AS series_code
"""


class RuleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    scope: Literal["series", "doc_type"]
    series_id: uuid.UUID | None = None
    table_name: str | None = Field(default=None, max_length=63)
    duration_months: int = Field(..., ge=0, le=1200)
    trigger_on: Literal[
        "document_date", "ingested_at", "reviewed_at", "archived_at"
    ] = "ingested_at"
    date_field: str | None = Field(default=None, max_length=63)
    final_action: Literal["destroy", "keep_forever", "review"] = "destroy"


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    duration_months: int | None = Field(default=None, ge=0, le=1200)
    trigger_on: (
        Literal["document_date", "ingested_at", "reviewed_at", "archived_at"] | None
    ) = None
    date_field: str | None = Field(default=None, max_length=63)
    final_action: Literal["destroy", "keep_forever", "review"] | None = None


def _check_date_field(
    trigger_on: str, date_field: str | None, locale: str
) -> str | None:
    if trigger_on != "document_date":
        return None
    field = _sanitize(date_field or "")
    if not date_field or field != date_field:
        raise HTTPException(
            status_code=422, detail=t("retention.date_field_required", locale)
        )
    return field


async def _rule_out(conn, tenant_id: str, rule_id: str) -> dict:
    row = (
        (
            await conn.execute(
                text(f"""
                SELECT {_RULE_COLS} FROM sdai_retention_rules r
                LEFT JOIN sdai_series s ON s.id = r.series_id
                WHERE r.id = CAST(:id AS uuid) AND r.tenant_id = CAST(:tid AS uuid)
            """),
                {"id": rule_id, "tid": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return {**dict(row), "created_at": _iso(row["created_at"])} if row else None


@router.get("/retention-rules", summary="Retention rules")
async def list_rules(current_user: CurrentUser = Depends(require_reviewer)):
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT {_RULE_COLS} FROM sdai_retention_rules r
                    LEFT JOIN sdai_series s ON s.id = r.series_id
                    WHERE r.tenant_id = CAST(:tid AS uuid)
                    ORDER BY r.scope DESC, s.path NULLS LAST, r.table_name, r.name
                """),
                    {"tid": current_user.tenant_id},
                )
            )
            .mappings()
            .all()
        )
        counts: dict[str, int] = {}
        for table in await _get_tables_columns(conn, current_user.tenant_slug):
            for rule_id, n in await conn.execute(
                text(f"""
                    SELECT retention_rule_id::text, count(*) FROM "{table}"
                    WHERE retention_rule_id IS NOT NULL
                      AND lifecycle_status IS DISTINCT FROM 'destroyed'
                    GROUP BY 1
                """)
            ):
                counts[rule_id] = counts.get(rule_id, 0) + n
    return [
        {
            **dict(r),
            "created_at": _iso(r["created_at"]),
            "document_count": counts.get(r["id"], 0),
        }
        for r in rows
    ]


@router.post("/retention-rules", summary="Create a retention rule", status_code=201)
async def create_rule(
    request: Request,
    body: RuleBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    locale = current_user.locale
    date_field = _check_date_field(body.trigger_on, body.date_field, locale)
    async with _engine().begin() as conn:
        series_id = table_name = None
        if body.scope == "series":
            if body.series_id is None:
                raise HTTPException(
                    status_code=422, detail=t("common.series_not_found", locale)
                )
            found = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM sdai_series WHERE id = CAST(:id AS uuid)"
                        " AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"id": str(body.series_id), "tid": current_user.tenant_id},
                )
            ).one_or_none()
            if found is None:
                raise HTTPException(
                    status_code=404, detail=t("common.series_not_found", locale)
                )
            series_id = str(body.series_id)
        else:
            table_name, _ = await _table(conn, current_user, body.table_name or "")
        existing = (
            await conn.execute(
                text("""
                    SELECT 1 FROM sdai_retention_rules WHERE tenant_id = CAST(:tid AS uuid)
                      AND ((scope = 'series' AND series_id = CAST(:s AS uuid))
                        OR (scope = 'doc_type' AND table_name = :t))
                """),
                {"tid": current_user.tenant_id, "s": series_id, "t": table_name},
            )
        ).one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=409, detail=t("retention.rule_exists", locale)
            )
        rule_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_retention_rules
                        (tenant_id, name, scope, series_id, table_name, duration_months,
                         trigger_on, date_field, final_action, created_by)
                    VALUES (CAST(:tid AS uuid), :n, :scope, CAST(:s AS uuid), :t, :d, :tr, :df,
                            :fa, CAST(:u AS uuid))
                    RETURNING id::text
                """),
                {
                    "tid": current_user.tenant_id,
                    "n": body.name,
                    "scope": body.scope,
                    "s": series_id,
                    "t": table_name,
                    "d": body.duration_months,
                    "tr": body.trigger_on,
                    "df": date_field,
                    "fa": body.final_action,
                    "u": current_user.id,
                },
            )
        ).scalar()
        changed = await retention.recompute(conn, current_user.tenant_id)
        out = await _rule_out(conn, current_user.tenant_id, rule_id)
    await log_action(
        action="retention_rule_created",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            **body.model_dump(mode="json"),
            "rule_id": rule_id,
            "documents_updated": changed,
        },
        ip_address=client_ip(request),
    )
    return out


@router.patch("/retention-rules/{rule_id}", summary="Change a retention rule")
async def update_rule(
    request: Request,
    rule_id: uuid.UUID,
    body: RulePatch,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    locale = current_user.locale
    async with _engine().begin() as conn:
        before = await _rule_out(conn, current_user.tenant_id, str(rule_id))
        if before is None:
            raise HTTPException(
                status_code=404, detail=t("retention.rule_not_found", locale)
            )
        changes = body.model_dump(exclude_unset=True)
        trigger_on = changes.get("trigger_on", before["trigger_on"])
        changes["date_field"] = _check_date_field(
            trigger_on, changes.get("date_field", before["date_field"]), locale
        )
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await conn.execute(
            text(
                f"UPDATE sdai_retention_rules SET {sets} WHERE id = CAST(:id AS uuid)"
            ),
            {**changes, "id": str(rule_id)},
        )
        changed = await retention.recompute(conn, current_user.tenant_id)
        out = await _rule_out(conn, current_user.tenant_id, str(rule_id))
    await log_action(
        action="retention_rule_updated",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "rule_id": str(rule_id),
            "changes": {
                k: {"old": before[k], "new": out[k]}
                for k in changes
                if before[k] != out[k]
            },
            "documents_updated": changed,
        },
        ip_address=client_ip(request),
    )
    return out


@router.delete("/retention-rules/{rule_id}", summary="Delete a retention rule")
async def delete_rule(
    request: Request,
    rule_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        before = await _rule_out(conn, current_user.tenant_id, str(rule_id))
        if before is None:
            raise HTTPException(
                status_code=404,
                detail=t("retention.rule_not_found", current_user.locale),
            )
        await conn.execute(
            text("DELETE FROM sdai_retention_rules WHERE id = CAST(:id AS uuid)"),
            {"id": str(rule_id)},
        )
        changed = await retention.recompute(conn, current_user.tenant_id)
    await log_action(
        action="retention_rule_deleted",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"rule": before, "documents_updated": changed},
        ip_address=client_ip(request),
    )
    return {"ok": True}


@router.post("/retention/run", summary="Run the daily retention job now")
async def run_retention(
    request: Request, current_user: CurrentUser = Depends(require_admin)
):
    """Admin: run the retention job + trash purge now (local node only)."""
    import ingest_router

    ingest_router._require_processing_node(current_user.locale)
    totals = await retention.run_once(_engine())
    await log_action(
        action="retention_job_run",
        user_id=current_user.id,
        user_email=current_user.email,
        details=totals,
        ip_address=client_ip(request),
    )
    return totals


# ── Legal holds ───────────────────────────────────────────────────────────────

HOLD_MAX_DOCUMENTS = 5000


class HoldBody(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)
    document: DocumentRef | None = None
    series_id: uuid.UUID | None = None
    search: SearchSelection | None = None

    @model_validator(mode="after")
    def _one_target(self):
        if (
            sum(x is not None for x in (self.document, self.series_id, self.search))
            != 1
        ):
            raise ValueError("give exactly one of document, series_id or search")
        return self


class ReleaseBody(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)


async def _hold_out(conn, tenant_id: str, where: str, params: dict) -> list[dict]:
    rows = (
        (
            await conn.execute(
                text(f"""
                SELECT lh.id::text AS id, lh.scope, lh.origin, lh.series_id::text AS series_id,
                       s.name AS series_name, lh.query, lh.reason, lh.created_at,
                       cu.email AS created_by, lh.released_at, ru.email AS released_by,
                       lh.release_reason,
                       (SELECT count(*) FROM sdai_legal_hold_documents d WHERE d.hold_id = lh.id)
                           AS document_count
                FROM sdai_legal_holds lh
                LEFT JOIN sdai_series s ON s.id = lh.series_id
                LEFT JOIN sdai_users cu ON cu.id = lh.created_by
                LEFT JOIN sdai_users ru ON ru.id = lh.released_by
                WHERE lh.tenant_id = CAST(:tid AS uuid) AND {where}
                ORDER BY lh.released_at IS NOT NULL, lh.created_at DESC
            """),
                {"tid": tenant_id, **params},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            **dict(r),
            "created_at": _iso(r["created_at"]),
            "released_at": _iso(r["released_at"]),
        }
        for r in rows
    ]


@router.get("/legal-holds", summary="Legal holds")
async def list_holds(
    include_released: bool = False,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        return await _hold_out(
            conn,
            current_user.tenant_id,
            "TRUE" if include_released else "lh.released_at IS NULL",
            {},
        )


@router.post("/legal-holds", summary="Place a legal hold", status_code=201)
async def create_hold(
    request: Request,
    body: HoldBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """On one document, a series (and everything below it, now and later)
    or the documents a search finds now (frozen). Blocks trash, purge and
    destruction until released."""
    require_lifecycle_manager(current_user)
    locale = current_user.locale
    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        docs: list[tuple[str, str]] = []
        series_id = None
        query = None
        if body.document is not None:
            origin, scope = "document", "documents"
            table = _sanitize(body.document.table_name)
            if table not in tables_cols:
                raise HTTPException(
                    status_code=404, detail=t("common.document_not_found", locale)
                )
            await _require_trashed_readable(
                conn, table, str(body.document.id), current_user
            )
            docs = [(table, str(body.document.id))]
        elif body.series_id is not None:
            origin, scope = "series", "series"
            series_id = str(body.series_id)
            found = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM sdai_series WHERE id = CAST(:id AS uuid)"
                        " AND tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"id": series_id, "tid": current_user.tenant_id},
                )
            ).one_or_none()
            if found is None:
                raise HTTPException(
                    status_code=404, detail=t("common.series_not_found", locale)
                )
        else:
            origin, scope = "search", "documents"
            query = body.search.model_dump(mode="json")
            docs = await _search_documents(conn, tables_cols, body.search, current_user)
            if not docs:
                raise HTTPException(
                    status_code=422, detail=t("legal_hold.nothing_found", locale)
                )
            if len(docs) > HOLD_MAX_DOCUMENTS:
                raise HTTPException(
                    status_code=413,
                    detail=t("legal_hold.too_many", locale, max=HOLD_MAX_DOCUMENTS),
                )
        hold_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_legal_holds
                        (tenant_id, scope, origin, series_id, query, reason, created_by)
                    VALUES (CAST(:tid AS uuid), :scope, :origin, CAST(:s AS uuid),
                            CAST(:q AS jsonb), :r, CAST(:u AS uuid))
                    RETURNING id::text
                """),
                {
                    "tid": current_user.tenant_id,
                    "scope": scope,
                    "origin": origin,
                    "s": series_id,
                    "q": json.dumps(query) if query is not None else None,
                    "r": body.reason,
                    "u": current_user.id,
                },
            )
        ).scalar()
        for table, doc_id in docs:
            await conn.execute(
                text("""
                    INSERT INTO sdai_legal_hold_documents (hold_id, table_name, document_id)
                    VALUES (CAST(:h AS uuid), :t, CAST(:d AS uuid)) ON CONFLICT DO NOTHING
                """),
                {"h": hold_id, "t": table, "d": doc_id},
            )
        await lifecycle.refresh_hold_flags(conn, sorted(tables_cols))
        out = (
            await _hold_out(
                conn,
                current_user.tenant_id,
                "lh.id = CAST(:id AS uuid)",
                {"id": hold_id},
            )
        )[0]
    await log_action(
        action="legal_hold_set",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=docs[0][0] if len(docs) == 1 else None,
        document_id=docs[0][1] if len(docs) == 1 else None,
        details={
            "hold_id": hold_id,
            "origin": origin,
            "reason": body.reason,
            "series_id": series_id,
            "query": query,
            "documents": len(docs),
        },
        ip_address=client_ip(request),
    )
    await _after_change()
    return out


async def _search_documents(
    conn, tables_cols, s: SearchSelection, user
) -> list[tuple[str, str]]:
    only_table = _sanitize(s.document_type) if s.document_type else None
    if only_table and only_table not in tables_cols:
        return []
    rows, _ = await _run_list_query(
        conn,
        tables_cols,
        only_table=only_table,
        q=s.q or None,
        confidence=s.confidence,
        review_status=s.review_status,
        date_from=s.date_from,
        date_to=s.date_to,
        reviewed_by=str(s.reviewed_by) if s.reviewed_by else None,
        page=1,
        page_size=HOLD_MAX_DOCUMENTS + 1,
        current_user=user,
        permission="read",
        with_details=False,
        series=str(s.series) if s.series else None,
        include_subseries=s.include_subseries,
        tag=str(s.tag) if s.tag else None,
    )
    return [(r["table_name"], r["id"]) for r in rows]


@router.post("/legal-holds/{hold_id}/release", summary="Release a legal hold")
async def release_hold(
    request: Request,
    hold_id: uuid.UUID,
    body: ReleaseBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        updated = (
            (
                await conn.execute(
                    text("""
                    UPDATE sdai_legal_holds
                    SET released_at = now(), released_by = CAST(:u AS uuid), release_reason = :r
                    WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
                      AND released_at IS NULL
                    RETURNING origin, (SELECT count(*) FROM sdai_legal_hold_documents
                                       WHERE hold_id = CAST(:id AS uuid)) AS n
                """),
                    {
                        "u": current_user.id,
                        "r": body.reason,
                        "id": str(hold_id),
                        "tid": current_user.tenant_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if updated is None:
            raise HTTPException(
                status_code=404, detail=t("legal_hold.not_found", current_user.locale)
            )
        tables = await _get_tables_columns(conn, current_user.tenant_slug)
        await lifecycle.refresh_hold_flags(conn, sorted(tables))
    await log_action(
        action="legal_hold_released",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "hold_id": str(hold_id),
            "origin": updated["origin"],
            "documents": updated["n"],
            "reason": body.reason,
        },
        ip_address=client_ip(request),
    )
    await _after_change()
    return {"ok": True}


# ── Destruction ───────────────────────────────────────────────────────────────


def destruction_http(exc: destruction.DestructionError, locale: str) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail=t(exc.key, locale, **exc.params)
    )


@router.get("/destruction/eligible", summary="Documents whose retention has ended")
async def list_eligible(current_user: CurrentUser = Depends(require_reviewer)):
    """destruction_eligible documents the caller can read, with their rule,
    hold and whether a pending proposal already lists them."""
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        pending = {
            (d["table_name"], d["id"]): rid
            for rid, docs in await conn.execute(
                text("""
                    SELECT id::text, documents FROM sdai_destruction_records
                    WHERE tenant_id = CAST(:tid AS uuid) AND status = 'proposed'
                """),
                {"tid": current_user.tenant_id},
            )
            for d in (json.loads(docs) if isinstance(docs, str) else docs)
        }
        items = []
        for table, cols in sorted(tables_cols.items()):
            access = _access_where_clause(table, current_user)
            display = _display_col(cols)
            q = f'"{table}"'
            rows = (
                (
                    await conn.execute(
                        text(f"""
                        SELECT {q}.id::text AS id, {q}.record_id,
                               {f"{q}.document_type::text" if "document_type" in cols else "NULL::text"}
                                   AS document_type,
                               {f'{q}."{display}"::text' if display else "NULL::text"} AS display,
                               {q}.retention_until, {q}.archived_at, r.name AS rule_name,
                               r.final_action, {lifecycle.held_sql(table)} AS held
                        FROM {q}
                        LEFT JOIN sdai_retention_rules r ON r.id = {q}.retention_rule_id
                        WHERE {q}.lifecycle_status = 'destruction_eligible'
                        {f"AND {access}" if access else ""}
                        ORDER BY {q}.retention_until, {q}.record_id
                    """),
                        _access_params(current_user),
                    )
                )
                .mappings()
                .all()
            )
            for r in rows:
                items.append(
                    {
                        "table_name": table,
                        **{k: _iso(v) for k, v in dict(r).items() if k != "held"},
                        "final_action": r["final_action"] or "review",
                        "legal_hold": bool(r["held"]),
                        "pending_destruction_id": pending.get((table, r["id"])),
                    }
                )
    return items


class ProposeBody(BaseModel):
    documents: list[DocumentRef] = Field(..., min_length=1, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)


def _record_out(r: dict, with_documents: bool = False) -> dict:
    docs = r.get("documents")
    if isinstance(docs, str):
        docs = json.loads(docs)
    out = {
        "id": r["id"],
        "number": destruction.record_number(r["id"], r.get("proposed_at")),
        "kind": r["kind"],
        "status": r["status"],
        "comment": r["comment"],
        "document_count": r["document_count"],
        "proposed_by": r.get("proposed_by_email"),
        "proposed_by_id": r.get("proposed_by"),
        "proposed_at": _iso(r.get("proposed_at")),
        "decided_by": r.get("decided_by_email"),
        "decided_at": _iso(r.get("decided_at")),
        "decision_note": r.get("decision_note"),
        "executed_at": _iso(r.get("executed_at")),
        "has_pv": bool(r.get("file_path")),
    }
    if with_documents:
        out["documents"] = docs
    return out


@router.get("/destructions", summary="Destruction records")
async def list_destructions(current_user: CurrentUser = Depends(require_reviewer)):
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT id::text FROM sdai_destruction_records
                    WHERE tenant_id = CAST(:tid AS uuid)
                    ORDER BY status <> 'proposed', proposed_at DESC LIMIT 500
                """),
                    {"tid": current_user.tenant_id},
                )
            )
            .scalars()
            .all()
        )
        return [
            _record_out(
                await destruction.load_record(conn, current_user.tenant_id, rid)
            )
            for rid in rows
        ]


@router.get("/destructions/{record_id}", summary="One destruction record")
async def get_destruction(
    record_id: uuid.UUID, current_user: CurrentUser = Depends(require_reviewer)
):
    require_lifecycle_manager(current_user)
    async with _engine().connect() as conn:
        try:
            r = await destruction.load_record(
                conn, current_user.tenant_id, str(record_id)
            )
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
    return _record_out(r, with_documents=True)


@router.post("/destructions", summary="Propose a destruction", status_code=201)
async def propose_destruction(
    request: Request,
    body: ProposeBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """A lifecycle manager proposes destroying destruction_eligible documents
    (they must be able to read each one). An admin other than the proposer
    approves."""
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        docs = []
        for ref in body.documents:
            table = _sanitize(ref.table_name)
            if table not in tables_cols:
                raise HTTPException(
                    status_code=404,
                    detail=t("common.document_not_found", current_user.locale),
                )
            await _require_document_permission(
                conn, table, str(ref.id), current_user, "read"
            )
            docs.append((table, str(ref.id)))
        try:
            record = await destruction.propose(conn, current_user, docs, body.comment)
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
        admins = [
            u
            for u in await retention.lifecycle_managers(conn, current_user.tenant_id)
            if u != current_user.id
        ]
        admins = (
            (
                await conn.execute(
                    text(
                        "SELECT id::text FROM sdai_users WHERE id = ANY(CAST(:ids AS uuid[]))"
                        " AND role = 'admin'"
                    ),
                    {"ids": admins},
                )
            )
            .scalars()
            .all()
        )
        await notifications.notify(
            conn,
            current_user.tenant_id,
            list(admins),
            "destruction_proposed",
            {
                "destruction_id": record["id"],
                "count": len(docs),
                "by": current_user.email,
            },
        )
    await log_action(
        action="destruction_proposed",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "destruction_id": record["id"],
            "documents": [
                {
                    "table_name": d["table_name"],
                    "id": d["id"],
                    "record_id": d["record_id"],
                }
                for d in record["documents"]
            ],
            "comment": body.comment,
        },
        ip_address=client_ip(request),
    )
    return {"id": record["id"], "document_count": len(docs)}


class DecisionBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post(
    "/destructions/{record_id}/approve", summary="Approve and execute a destruction"
)
async def approve_destruction(
    request: Request,
    record_id: uuid.UUID,
    body: DecisionBody | None = None,
    current_user: CurrentUser = Depends(require_admin),
):
    """Admin, never the proposer. Destroys every listed document in one
    transaction (409 and nothing destroyed if one can't be - e.g. a legal
    hold placed meanwhile), then deletes the files on this node, lists them
    for the peer, and generates the PV."""
    note = body.note if body else None
    async with _engine().begin() as conn:
        try:
            record = await destruction.decide(
                conn, current_user, str(record_id), True, note
            )
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
        except lifecycle.LifecycleError as exc:
            raise lifecycle_http(exc, current_user.locale) from None
    await destruction.finish(_engine(), current_user.tenant_id, str(record_id))
    docs = record["documents"]
    docs = json.loads(docs) if isinstance(docs, str) else docs
    await log_action(
        action="destruction_approved",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "destruction_id": str(record_id),
            "kind": record["kind"],
            "proposed_by": record["proposed_by_email"],
            "documents": len(docs),
            "files": len(record["files"]),
            "note": note,
        },
        ip_address=client_ip(request),
    )
    for d in docs:
        await log_action(
            action="document_destroyed",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=d["table_name"],
            document_id=d["id"],
            details={"destruction_id": str(record_id), "record_id": d["record_id"]},
        )
    await _after_change()
    return {"ok": True, "documents": len(docs), "files": len(record["files"])}


@router.post(
    "/destructions/{record_id}/reject", summary="Reject a destruction proposal"
)
async def reject_destruction(
    request: Request,
    record_id: uuid.UUID,
    body: DecisionBody | None = None,
    current_user: CurrentUser = Depends(require_admin),
):
    note = body.note if body else None
    async with _engine().begin() as conn:
        try:
            await destruction.decide(conn, current_user, str(record_id), False, note)
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
    await log_action(
        action="destruction_rejected",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"destruction_id": str(record_id), "note": note},
        ip_address=client_ip(request),
    )
    return {"ok": True}


@router.post(
    "/destructions/{record_id}/cancel", summary="Withdraw a destruction proposal"
)
async def cancel_destruction(
    request: Request,
    record_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_reviewer),
):
    require_lifecycle_manager(current_user)
    async with _engine().begin() as conn:
        try:
            await destruction.cancel(conn, current_user, str(record_id))
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
    await log_action(
        action="destruction_cancelled",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"destruction_id": str(record_id)},
        ip_address=client_ip(request),
    )
    return {"ok": True}


@router.get(
    "/destructions/{record_id}/pv", summary="Download the destruction report (PDF)"
)
async def download_pv(
    request: Request,
    record_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """The procès-verbal of an executed destruction. Generated again (new
    file) if it is missing on this node - the record holds everything it
    shows."""
    require_lifecycle_manager(current_user)
    engine = _engine()
    async with engine.connect() as conn:
        try:
            r = await destruction.load_record(
                conn, current_user.tenant_id, str(record_id)
            )
        except destruction.DestructionError as exc:
            raise destruction_http(exc, current_user.locale) from None
    if r["executed_at"] is None:
        raise HTTPException(
            status_code=404, detail=t("destruction.no_pv", current_user.locale)
        )
    path = Path(r["file_path"]) if r["file_path"] else None
    if path is None or not path.is_file():
        path = await destruction.write_pv(
            engine, current_user.tenant_id, str(record_id)
        )
    await log_action(
        action="destruction_pv_downloaded",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"destruction_id": str(record_id)},
        ip_address=client_ip(request),
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{destruction.record_number(r['id'], r['proposed_at'])}.pdf",
    )
