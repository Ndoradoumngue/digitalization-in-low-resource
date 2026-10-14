"""
Check-out / check-in of one filed document (GED Phase 7.2) - see locks.py.

Endpoints:
  GET  /api/db/documents/{tbl}/{id}/lock       lock state + what the caller may do (read)
  POST /api/db/documents/{tbl}/{id}/checkout   lock it for {hours} (edit; holder: extend)
  POST /api/db/documents/{tbl}/{id}/checkin    release it, optionally with a new version
                                               (multipart: file?, comment?, reextract?) - holder
  POST /api/db/documents/{tbl}/{id}/unlock     force release (admin) - the holder is notified

Audit: document_checked_out, document_checked_in (details.reason: checkin /
forced / expired).
"""

import locks
import notifications
import versions_router
from audit import client_ip, log_action
from auth import CurrentUser, require_admin, require_reviewer, require_user
from cache import invalidate_cache
from documents_router import (
    _document_visible,
    _get_tables_columns,
    _require_document_permission,
    _sanitize,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from i18n import t
from ingest_router import _engine
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text

router = APIRouter(prefix="/api/db", tags=["checkout"])


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _http(exc: locks.LockError, locale: str) -> HTTPException:
    return HTTPException(
        status_code=exc.status, detail=t(exc.key, locale, **exc.params)
    )


async def _table_or_404(conn, table_name: str, current_user: CurrentUser) -> str:
    safe = _sanitize(table_name)
    if safe not in await _get_tables_columns(conn, current_user.tenant_slug):
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe


async def _lock_view(conn, safe: str, doc_id: str, current_user: CurrentUser) -> dict:
    lock = await locks.state(conn, safe, doc_id) or {}
    active = bool(lock.get("active"))
    mine = active and lock.get("locked_by") == current_user.id
    can_edit = await _document_visible(conn, safe, doc_id, current_user, "edit")
    return {
        "locked": active,
        "locked_by": lock.get("locked_by") if active else None,
        "locked_by_name": locks.holder_label(lock) if active else None,
        "locked_until": _iso(lock.get("locked_until")) if active else None,
        "mine": mine,
        "can_checkout": can_edit and current_user.role in ("admin", "reviewer"),
        "can_checkin": mine,
        "can_force_unlock": active and not mine and current_user.role == "admin",
        "default_hours": locks.CHECKOUT_DEFAULT_HOURS,
        "max_hours": locks.CHECKOUT_MAX_HOURS,
    }


@router.get(
    "/documents/{table_name}/{doc_id}/lock", summary="Check-out state of a document"
)
async def get_lock(
    table_name: str, doc_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        return await _lock_view(conn, safe, doc_id, current_user)


class CheckoutBody(BaseModel):
    hours: int | None = Field(None, ge=1, le=720)


@router.post(
    "/documents/{table_name}/{doc_id}/checkout", summary="Check a document out"
)
@limiter.limit("30/minute")
async def checkout(
    request: Request,
    table_name: str,
    doc_id: str,
    body: CheckoutBody | None = None,
    current_user: CurrentUser = Depends(require_reviewer),
):
    hours = min(
        (body.hours if body else None) or locks.CHECKOUT_DEFAULT_HOURS,
        locks.CHECKOUT_MAX_HOURS,
    )
    async with _engine().begin() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        try:
            result = await locks.checkout(conn, safe, doc_id, current_user.id, hours)
        except locks.LockError as exc:
            raise _http(exc, current_user.locale) from None
    await log_action(
        action="document_checked_out",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "hours": hours,
            "until": _iso(result["locked_until"]),
            "extended": result["extended"],
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    async with _engine().connect() as conn:
        return await _lock_view(conn, safe, doc_id, current_user)


@router.post(
    "/documents/{table_name}/{doc_id}/checkin", summary="Check a document back in"
)
@limiter.limit("20/minute")
async def checkin(
    request: Request,
    table_name: str,
    doc_id: str,
    file: UploadFile | None = File(None),
    comment: str | None = Form(None, max_length=500),
    reextract: bool = Form(False),
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Release the caller's own check-out. With a file, it first becomes
    the document's next version (with `comment`) - the lock is released
    only once the version is stored."""
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        lock = await locks.state(conn, safe, doc_id)
    if not lock or not lock["active"] or lock["locked_by"] != current_user.id:
        raise HTTPException(
            status_code=409, detail=t("locks.not_holder", current_user.locale)
        )
    version = None
    if file is not None and file.filename:
        version = await versions_router.add_uploaded_version(
            request, safe, doc_id, file, comment, reextract, current_user
        )
    async with _engine().begin() as conn:
        try:
            await locks.release(conn, safe, doc_id, holder=current_user.id)
        except locks.LockError as exc:
            raise _http(exc, current_user.locale) from None
    await log_action(
        action="document_checked_in",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "reason": "checkin",
            "comment": comment,
            "version_no": version["version"]["version_no"] if version else None,
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    async with _engine().connect() as conn:
        view = await _lock_view(conn, safe, doc_id, current_user)
    return {
        **view,
        "version": version["version"] if version else None,
        "batch_id": version["batch_id"] if version else None,
    }


@router.post(
    "/documents/{table_name}/{doc_id}/unlock", summary="Force a check-out open (admin)"
)
async def force_unlock(
    request: Request,
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_admin),
):
    async with _engine().begin() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        try:
            lock = await locks.release(conn, safe, doc_id)
        except locks.LockError as exc:
            raise _http(exc, current_user.locale) from None
        record_id = (
            await conn.execute(
                text(f'SELECT record_id FROM "{safe}" WHERE id = CAST(:id AS uuid)'),
                {"id": doc_id},
            )
        ).scalar()
        await notifications.notify(
            conn,
            current_user.tenant_id,
            [lock["locked_by"]],
            "lock_released",
            {
                "record_id": record_id,
                "by": current_user.full_name or current_user.email,
            },
            table_name=safe,
            document_id=doc_id,
            exclude=current_user.id,
        )
    await log_action(
        action="document_checked_in",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "reason": "forced",
            "holder": lock["locked_by"],
            "record_id": record_id,
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    async with _engine().connect() as conn:
        return await _lock_view(conn, safe, doc_id, current_user)
