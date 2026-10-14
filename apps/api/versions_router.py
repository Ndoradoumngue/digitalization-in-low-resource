"""
File versions and change history of one filed document (GED Phase 2).

Endpoints:
  GET  /api/db/documents/{tbl}/{id}/versions               - every version, newest first (read)
  POST /api/db/documents/{tbl}/{id}/versions               - upload a new version, optionally
                                                             re-extracting its fields (edit)
  POST /api/db/documents/{tbl}/{id}/versions/{n}/restore   - make version n current again,
                                                             as a NEW version (edit)
  GET  /api/db/documents/{tbl}/{id}/versions/{n}/file      - download one version's file (download)
  GET  /api/db/documents/{tbl}/{id}/download[?version=N]   - the original file as
                                                             '<record id>_<name>', audited,
                                                             409 while sync copies it (download)
  GET  /api/db/documents/{tbl}/{id}/history                - field changes + audit events (read;
                                                             viewed/downloaded events: access
                                                             managers only)

Nothing here ever rewrites history: a version row is never updated or
deleted, a file is never overwritten (new files go to
VERSIONS_DIR/<tenant>/<document id>/v<N>_<name>, opened exclusively), and a
restore is a new version pointing at the old file.
"""

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from audit import client_ip, log_action
from auth import CurrentUser, require_reviewer, require_user
from cache import invalidate_cache
import document_files
import documents_router
import lifecycle
import ingest_router
from documents_router import (
    _get_tables_columns,
    _require_document_permission,
    _sanitize,
)
from export_format import serial
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from i18n import t
from ingest_router import (
    ALLOWED_EXTS,
    MAX_UPLOAD_BYTES,
    _create_batch,
    _engine,
    _mime_for,
    _resolve_preset_for_batch,
    _sniff_matches_extension,
)
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text
from sync import files as sync_files

router = APIRouter(prefix="/api/db", tags=["versions"])

_VERSION_COLS = """
    v.version_no, v.file_hash, v.size, v.mime, v.original_filename, v.comment,
    v.restored_from, v.created_by, u.email AS created_by_email, v.created_at
"""


async def _table_or_404(conn, table_name: str, current_user: CurrentUser) -> str:
    safe = _sanitize(table_name)
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    if safe not in tables_cols:
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe


def _safe_filename(name: str) -> str:
    """Basename only, restricted to a portable character set - the stored
    name is v<N>_<this>, never a path the client controls."""
    base = Path(name or "").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    stem, ext = Path(base).stem[:100], Path(base).suffix.lower()
    return f"{stem}{ext}"


async def _lock_for_change(
    conn, table: str, doc_id: str, current_user: CurrentUser
) -> int:
    """Row-lock the document for a version change and return the next
    version number. Refuses while someone else holds the check-out lock
    (409) or when the document is archived / destruction-eligible, unless
    the caller manages lifecycle (403)."""
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT current_version, locked_by::text AS locked_by, locked_until, lifecycle_status"
                    f' FROM "{table}" WHERE id = CAST(:id AS uuid) FOR UPDATE'
                ),
                {"id": doc_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=t("common.document_not_found", current_user.locale)
        )
    locked_until = row["locked_until"]
    if (
        row["locked_by"]
        and row["locked_by"] != current_user.id
        and (locked_until is None or locked_until > datetime.now(timezone.utc))
    ):
        raise HTTPException(
            status_code=409, detail=t("versions.document_locked", current_user.locale)
        )
    if not lifecycle.can_edit_status(
        row["lifecycle_status"], current_user.can_manage_document_lifecycle
    ):
        raise HTTPException(
            status_code=403, detail=t("versions.document_archived", current_user.locale)
        )
    latest = (
        await conn.execute(
            text("""
                SELECT COALESCE(MAX(version_no), 0) FROM sdai_document_versions
                WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                  AND document_id = CAST(:id AS uuid)
            """),
            {"tid": current_user.tenant_id, "t": table, "id": doc_id},
        )
    ).scalar() or 0
    return max(latest, row["current_version"] or 0) + 1


async def _insert_version(
    conn, table: str, doc_id: str, current_user: CurrentUser, **cols
) -> dict:
    """Insert one version row and make it the document's current version."""
    row = (
        (
            await conn.execute(
                text("""
                INSERT INTO sdai_document_versions
                    (tenant_id, table_name, document_id, version_no, file_path, file_hash,
                     size, mime, original_filename, comment, restored_from, created_by)
                VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), :version_no, :file_path,
                        :file_hash, :size, :mime, :original_filename, :comment,
                        :restored_from, CAST(:by AS uuid))
                RETURNING version_no, file_hash, size, mime, original_filename, comment,
                          restored_from, created_by, created_at
            """),
                {
                    "tid": current_user.tenant_id,
                    "t": table,
                    "id": doc_id,
                    "by": current_user.id,
                    **cols,
                },
            )
        )
        .mappings()
        .one()
    )
    await conn.execute(
        text(f'UPDATE "{table}" SET current_version = :n WHERE id = CAST(:id AS uuid)'),
        {"n": cols["version_no"], "id": doc_id},
    )
    return {
        **{k: serial(v) for k, v in dict(row).items()},
        "created_by_email": current_user.email,
        "is_current": True,
    }


async def _start_reextraction(
    table: str,
    doc_id: str,
    version_no: int,
    file_path: Path,
    file_hash: str,
    filename: str,
    current_user: CurrentUser,
) -> str:
    """Run a version's file through the ingest pipeline into this same
    document (see ingest_router._apply_reextraction). Same prompt preset as
    the document's own upload. Returns the batch id, pollable through
    GET /api/ingest/status/{batch_id}."""
    async with _engine().connect() as conn:
        doc_batch = (
            await conn.execute(
                text(
                    f'SELECT batch_id::text FROM "{table}" WHERE id = CAST(:id AS uuid)'
                ),
                {"id": doc_id},
            )
        ).scalar()
    preset_id = await _resolve_preset_for_batch(doc_batch) if doc_batch else None
    batch_id = await _create_batch(
        "version",
        current_user.tenant_id,
        created_by=current_user.id,
        preset_id=preset_id,
    )
    async with _engine().begin() as conn:
        bd_id = (
            await conn.execute(
                text("""
                    INSERT INTO batch_documents
                        (batch_id, filename, source_path, target_table, target_document_id,
                         target_version)
                    VALUES (CAST(:b AS uuid), :f, :sp, :t, CAST(:id AS uuid), :n)
                    RETURNING id
                """),
                {
                    "b": batch_id,
                    "f": filename,
                    "sp": str(file_path),
                    "t": table,
                    "id": doc_id,
                    "n": version_no,
                },
            )
        ).scalar()
    await ingest_router._submit(
        (
            str(bd_id),
            batch_id,
            file_path,
            filename,
            file_hash,
            current_user.tenant_id,
            current_user.tenant_slug,
        )
    )
    return batch_id


# ── List ──────────────────────────────────────────────────────────────────────


@router.get(
    "/documents/{table_name}/{doc_id}/versions", summary="File versions of a document"
)
async def list_versions(
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        current = (
            await conn.execute(
                text(
                    f'SELECT current_version FROM "{safe}" WHERE id = CAST(:id AS uuid)'
                ),
                {"id": doc_id},
            )
        ).scalar()
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT {_VERSION_COLS}
                    FROM sdai_document_versions v
                    LEFT JOIN sdai_users u ON u.id = v.created_by
                    WHERE v.tenant_id = CAST(:tid AS uuid) AND v.table_name = :t
                      AND v.document_id = CAST(:id AS uuid)
                    ORDER BY v.version_no DESC
                """),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
    return {
        "current_version": current,
        "versions": [
            {
                **{k: serial(v) for k, v in dict(r).items()},
                "is_current": r["version_no"] == current,
            }
            for r in rows
        ],
    }


# ── Upload a new version ─────────────────────────────────────────────────────


@router.post(
    "/documents/{table_name}/{doc_id}/versions", summary="Upload a new file version"
)
@limiter.limit("20/minute")
async def upload_version(
    request: Request,
    table_name: str,
    doc_id: str,
    file: UploadFile = File(...),
    comment: str | None = Form(None, max_length=500),
    reextract: bool = Form(False),
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Attach a new file to the document as version N+1 (it becomes the
    current version). With reextract=true the file is also run through the
    extraction pipeline and its fields written into this same document -
    the previous values stay in the field history."""
    return await add_uploaded_version(
        request, table_name, doc_id, file, comment, reextract, current_user
    )


async def add_uploaded_version(
    request: Request,
    table_name: str,
    doc_id: str,
    file: UploadFile,
    comment: str | None,
    reextract: bool,
    current_user: CurrentUser,
) -> dict:
    """upload_version's work - also used by check-in (checkout_router)."""
    locale = current_user.locale
    filename = _safe_filename(file.filename or "")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=422,
            detail=t(
                "ingest.unsupported_file_type",
                locale,
                ext=ext,
                filename=file.filename,
                allowed=", ".join(sorted(ALLOWED_EXTS)),
            ),
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=t(
                "ingest.file_too_large",
                locale,
                filename=filename,
                max_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
            ),
        )
    if not _sniff_matches_extension(content, ext):
        raise HTTPException(
            status_code=422,
            detail=t("ingest.invalid_file_content", locale, filename=filename, ext=ext),
        )
    file_hash = hashlib.sha256(content).hexdigest()

    written = False
    try:
        async with _engine().begin() as conn:
            safe = await _table_or_404(conn, table_name, current_user)
            await _require_document_permission(conn, safe, doc_id, current_user, "edit")
            version_no = await _lock_for_change(conn, safe, doc_id, current_user)

            dest_dir = ingest_router.VERSIONS_DIR / current_user.tenant_slug / doc_id
            dest = dest_dir / f"v{version_no}_{filename}"

            def _write() -> None:
                dest_dir.mkdir(parents=True, exist_ok=True)
                with open(dest, "xb") as fh:  # never overwrite an existing file
                    fh.write(content)

            await asyncio.to_thread(_write)
            written = True
            version = await _insert_version(
                conn,
                safe,
                doc_id,
                current_user,
                version_no=version_no,
                file_path=str(dest),
                file_hash=file_hash,
                size=len(content),
                mime=_mime_for(filename),
                original_filename=file.filename or filename,
                comment=comment,
                restored_from=None,
            )
    except FileExistsError:
        raise HTTPException(status_code=409, detail=t("versions.conflict", locale))
    except BaseException:
        # The transaction rolled back - don't leave an unreferenced file.
        if written:
            dest.unlink(missing_ok=True)
        raise

    batch_id = None
    if reextract:
        batch_id = await _start_reextraction(
            safe, doc_id, version_no, dest, file_hash, filename, current_user
        )

    await log_action(
        action="document_version_added",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "version_no": version_no,
            "file_hash": file_hash,
            "size": len(content),
            "original_filename": file.filename,
            "reextract": reextract,
            **({"batch_id": batch_id} if batch_id else {}),
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    return {"version": version, "batch_id": batch_id}


# ── Restore ───────────────────────────────────────────────────────────────────


class RestoreVersionBody(BaseModel):
    comment: str | None = Field(None, max_length=500)
    reextract: bool = False


@router.post(
    "/documents/{table_name}/{doc_id}/versions/{version_no}/restore",
    summary="Make an earlier version current again (as a new version)",
)
@limiter.limit("20/minute")
async def restore_version(
    request: Request,
    table_name: str,
    doc_id: str,
    version_no: int,
    body: RestoreVersionBody | None = None,
    current_user: CurrentUser = Depends(require_reviewer),
):
    body = body or RestoreVersionBody()
    locale = current_user.locale
    async with _engine().begin() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        new_no = await _lock_for_change(conn, safe, doc_id, current_user)
        source = (
            (
                await conn.execute(
                    text("""
                    SELECT file_path, file_hash, size, mime, original_filename
                    FROM sdai_document_versions
                    WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                      AND document_id = CAST(:id AS uuid) AND version_no = :n
                """),
                    {
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                        "n": version_no,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if source is None:
            raise HTTPException(status_code=404, detail=t("versions.not_found", locale))
        current = (
            await conn.execute(
                text(
                    f'SELECT current_version FROM "{safe}" WHERE id = CAST(:id AS uuid)'
                ),
                {"id": doc_id},
            )
        ).scalar()
        if current == version_no:
            raise HTTPException(
                status_code=422, detail=t("versions.already_current", locale)
            )
        version = await _insert_version(
            conn,
            safe,
            doc_id,
            current_user,
            version_no=new_no,
            file_path=source["file_path"],
            file_hash=source["file_hash"],
            size=source["size"],
            mime=source["mime"],
            original_filename=source["original_filename"],
            comment=body.comment,
            restored_from=version_no,
        )

    batch_id = None
    if body.reextract:
        path = Path(source["file_path"])
        if not path.is_file():
            raise HTTPException(
                status_code=422, detail=t("versions.file_missing", locale)
            )
        batch_id = await _start_reextraction(
            safe,
            doc_id,
            new_no,
            path,
            source["file_hash"] or "",
            path.name,
            current_user,
        )

    await log_action(
        action="document_version_restored",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "version_no": new_no,
            "restored_from": version_no,
            "reextract": body.reextract,
            **({"batch_id": batch_id} if batch_id else {}),
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    return {"version": version, "batch_id": batch_id}


# ── Download ──────────────────────────────────────────────────────────────────


@router.get(
    "/documents/{table_name}/{doc_id}/versions/{version_no}/file",
    summary="Download one version's file",
)
@limiter.limit("60/minute")
async def download_version(
    request: Request,
    table_name: str,
    doc_id: str,
    version_no: int,
    inline: bool = Query(
        False, description="Open in the browser instead of downloading"
    ),
    current_user: CurrentUser = Depends(require_user),
):
    locale = current_user.locale
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "download")
        row = (
            (
                await conn.execute(
                    text("""
                    SELECT file_path, mime, original_filename FROM sdai_document_versions
                    WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                      AND document_id = CAST(:id AS uuid) AND version_no = :n
                """),
                    {
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                        "n": version_no,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(status_code=404, detail=t("versions.not_found", locale))
    path = Path(row["file_path"]).resolve()
    if not any(path.is_relative_to(root) for root in documents_router._SAFE_FILE_ROOTS):
        raise HTTPException(status_code=404, detail=t("common.file_not_found", locale))
    if not path.is_file():
        async with _engine().connect() as conn:
            if await sync_files.file_pending(conn, row["file_path"]):
                raise sync_files.pending_exception(locale)
        raise HTTPException(status_code=404, detail=t("common.file_not_found", locale))

    await log_action(
        action="document_downloaded",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"version_no": version_no, "via": "version"},
        ip_address=client_ip(request),
    )
    return FileResponse(
        path,
        media_type=row["mime"] or None,
        filename=row["original_filename"] or path.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get(
    "/documents/{table_name}/{doc_id}/download",
    summary="Download the document's original file (current or a given version)",
)
@limiter.limit("60/minute")
async def download_document(
    request: Request,
    table_name: str,
    doc_id: str,
    version: int | None = Query(None, ge=1, description="Version number - default: the current one"),
    inline: bool = Query(False, description="Open in the browser instead of downloading"),
    current_user: CurrentUser = Depends(require_user),
):
    """The untouched file of the current version (version 1 = the ingested
    original) or of `version`, named '<record id>_<original name>'. Needs
    the download permission; always audited (document_downloaded). 409
    when local/cloud sync has not copied the file to this node yet.
    Confidential/secret PDFs come watermarked when
    DOWNLOAD_WATERMARK_CONFIDENTIAL=true."""
    locale = current_user.locale
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        safe = _sanitize(table_name)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", locale, table=safe)
            )
        await _require_document_permission(conn, safe, doc_id, current_user, "download")
        f = await document_files.resolve(
            conn, safe, tables_cols[safe], doc_id, current_user.tenant_id, version
        )
        if f is None:
            raise HTTPException(status_code=404, detail=t("versions.not_found", locale))
        path = await document_files.check_servable(conn, f, locale)

    filename = document_files.download_name(f.record_id, f.original_filename, path.name)
    response, watermarked = await document_files.deliver(
        f, path, filename, inline=inline, who=current_user.email, locale=locale
    )
    await log_action(
        action="document_downloaded",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "version_no": f.version_no,
            "via": "download",
            "file_hash": f.file_hash,
            **({"watermarked": True} if watermarked else {}),
        },
        ip_address=client_ip(request),
    )
    return response


# ── History ───────────────────────────────────────────────────────────────────

# Who looked at or downloaded a document is access tracking, not document
# history - only admins and access managers see those events.
_ACCESS_TRACKING_ACTIONS = ("document_viewed", "document_downloaded", "share_link_accessed")


@router.get(
    "/documents/{table_name}/{doc_id}/history", summary="Field changes and audit events"
)
async def document_history(
    table_name: str,
    doc_id: str,
    limit: int = Query(200, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_user),
):
    """Every recorded field change (old -> new, who, how, under which file
    version) and the document's audit events, newest first. IP addresses
    are never returned here (the admin audit log has them)."""
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        fields = (
            (
                await conn.execute(
                    text("""
                    SELECT h.id, h.field, h.old_value, h.new_value, h.change_source, h.version_no,
                           h.changed_by, u.email AS changed_by_email, h.created_at
                    FROM sdai_field_history h
                    LEFT JOIN sdai_users u ON u.id = h.changed_by
                    WHERE h.tenant_id = CAST(:tid AS uuid) AND h.table_name = :t
                      AND h.document_id = CAST(:id AS uuid)
                    ORDER BY h.created_at DESC, h.field
                    LIMIT :limit
                """),
                    {
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        hide_tracking = not current_user.can_manage_document_access
        events = (
            (
                await conn.execute(
                    text(f"""
                    SELECT id, action, user_id, user_email, details, created_at
                    FROM sdai_audit_log
                    WHERE table_name = :t AND document_id = CAST(:id AS uuid)
                      {"AND NOT (action = ANY(:hidden))" if hide_tracking else ""}
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                    {
                        "t": safe,
                        "id": doc_id,
                        "limit": limit,
                        "hidden": list(_ACCESS_TRACKING_ACTIONS),
                    },
                )
            )
            .mappings()
            .all()
        )
    return {
        "fields": [{k: serial(v) for k, v in dict(r).items()} for r in fields],
        "events": [{k: serial(v) for k, v in dict(r).items()} for r in events],
    }
