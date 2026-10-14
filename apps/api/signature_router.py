"""
Electronic signatures of a filed document (GED Phase 7.4) - see signatures.py.
Simple / advanced electronic signatures (PAdES); never "qualified".

Endpoints:
  GET  /api/db/signing/status                                  is signing set up on this node
  GET  /api/db/signature-requests/pending                      requests waiting for MY signature
  GET  /api/db/documents/{tbl}/{id}/signature-requests         requests on a document (read)
  POST /api/db/documents/{tbl}/{id}/signature-requests         {signers[{user_id, title?}], reason,
                                                               message?} (edit) - signers in order
  POST /api/db/signature-requests/{id}/sign                    {password, reason?} - the next signer
  POST /api/db/signature-requests/{id}/decline                 {note} - the next signer
  POST /api/db/signature-requests/{id}/cancel                  the requester or an admin
  GET  /api/db/documents/{tbl}/{id}/signatures[?version=N]     evidence + PAdES verification of a
                                                               version (default: current) + audit

Signing: the signer re-enters their password; the server signs the
document's current version with the organisation certificate (visible
stamp: name, title, date, reason) and stores the result as a NEW version.
A non-PDF original (image) is first converted to PDF - PDF/A when ocrmypdf
is installed - as a version of its own. Signing respects the check-out lock
and the lifecycle read-only rule like any version change.

Audit: signature_requested, document_signed, signature_declined,
signature_request_cancelled, signature_password_failed.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import document_files
import ingest_router
import notifications
import signatures
from audit import client_ip, log_action
from auth import CurrentUser, require_reviewer, require_user
from cache import invalidate_cache
from documents_router import (
    _get_tables_columns,
    _require_document_permission,
    _sanitize,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from i18n import t
from ingest_router import _engine
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text
from versions_router import _insert_version, _lock_for_change

router = APIRouter(prefix="/api/db", tags=["signatures"])

MAX_SIGNERS = 10
# Evidence/audit actions shown next to a verification.
_EVIDENCE_ACTIONS = (
    "signature_requested",
    "document_signed",
    "signature_declined",
    "signature_request_cancelled",
)
_ROLE_TITLES = {
    "admin": {"fr": "Administrateur", "en": "Administrator"},
    "reviewer": {"fr": "Agent", "en": "Officer"},
    "reader": {"fr": "Agent", "en": "Officer"},
}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


async def _table_or_404(
    conn, table_name: str, current_user: CurrentUser
) -> tuple[str, dict]:
    safe = _sanitize(table_name)
    tables = await _get_tables_columns(conn, current_user.tenant_slug)
    if safe not in tables:
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe, tables[safe]


@router.get("/signing/status", summary="Is electronic signature available on this node")
async def signing_status(current_user: CurrentUser = Depends(require_user)):
    info = signatures.status()
    if current_user.role != "admin":
        info.pop("reason", None)
    return info


# ── Requests ──────────────────────────────────────────────────────────────────


_REQUEST_COLS = """
    r.id::text AS id, r.table_name, r.document_id::text AS document_id,
    r.requested_by::text AS requested_by, rb.full_name AS requested_by_name,
    rb.email AS requested_by_email, r.reason, r.message, r.signers, r.status,
    r.created_at, r.closed_at, r.closed_by::text AS closed_by, r.close_note
"""


async def _names(conn, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await conn.execute(
        text(
            "SELECT id::text, COALESCE(full_name, email) FROM sdai_users"
            " WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": ids},
    )
    return {r[0]: r[1] for r in rows}


def _next_signer(signers: list[dict]) -> dict | None:
    return next((s for s in signers if s["status"] == "pending"), None)


async def _request_out(conn, row: dict, current_user: CurrentUser) -> dict:
    signers = _json(row["signers"])
    names = await _names(conn, [s["user_id"] for s in signers])
    nxt = _next_signer(signers) if row["status"] == "pending" else None
    return {
        "id": row["id"],
        "table_name": row["table_name"],
        "document_id": row["document_id"],
        "requested_by": row["requested_by"],
        "requested_by_name": row["requested_by_name"] or row["requested_by_email"],
        "reason": row["reason"],
        "message": row["message"],
        "status": row["status"],
        "created_at": _iso(row["created_at"]),
        "closed_at": _iso(row["closed_at"]),
        "close_note": row["close_note"],
        "signers": [
            {**s, "name": names.get(s["user_id"], "?"), "is_next": nxt is s}
            for s in signers
        ],
        "can_sign": bool(nxt and nxt["user_id"] == current_user.id),
        "can_cancel": row["status"] == "pending"
        and (row["requested_by"] == current_user.id or current_user.role == "admin"),
    }


async def _load_request(
    conn, request_id: str, current_user: CurrentUser, *, lock=False
) -> dict:
    rid = _uuid(request_id)
    row = None
    if rid:
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_REQUEST_COLS} FROM sdai_signature_requests r"
                        " LEFT JOIN sdai_users rb ON rb.id = r.requested_by"
                        " WHERE r.id = CAST(:id AS uuid) AND r.tenant_id = CAST(:tid AS uuid)"
                        + (" FOR UPDATE OF r" if lock else "")
                    ),
                    {"id": rid, "tid": current_user.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=t("signatures.request_not_found", current_user.locale),
        )
    safe, _ = await _table_or_404(conn, row["table_name"], current_user)
    await _require_document_permission(
        conn, safe, row["document_id"], current_user, "read"
    )
    return dict(row)


@router.get("/signature-requests/pending", summary="Signature requests waiting for me")
async def my_pending_requests(current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT {_REQUEST_COLS} FROM sdai_signature_requests r
                    LEFT JOIN sdai_users rb ON rb.id = r.requested_by
                    WHERE r.tenant_id = CAST(:tid AS uuid) AND r.status = 'pending'
                      AND r.signers @> CAST(:me AS jsonb)
                    ORDER BY r.created_at
                """),
                    {
                        "tid": current_user.tenant_id,
                        "me": json.dumps(
                            [{"user_id": current_user.id, "status": "pending"}]
                        ),
                    },
                )
            )
            .mappings()
            .all()
        )
        tables = await _get_tables_columns(conn, current_user.tenant_slug)
        out = []
        for r in rows:
            if r["table_name"] not in tables:
                continue
            item = await _request_out(conn, dict(r), current_user)
            if not item["can_sign"]:
                continue  # an earlier signer has not signed yet
            try:
                await _require_document_permission(
                    conn, r["table_name"], r["document_id"], current_user, "read"
                )
            except HTTPException:
                continue
            item["record_id"] = (
                await conn.execute(
                    text(
                        f'SELECT record_id FROM "{r["table_name"]}" WHERE id = CAST(:id AS uuid)'
                    ),
                    {"id": r["document_id"]},
                )
            ).scalar()
            out.append(item)
    return out


@router.get(
    "/documents/{table_name}/{doc_id}/signature-requests",
    summary="Signature requests on a document",
)
async def list_requests(
    table_name: str, doc_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().connect() as conn:
        safe, _ = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT {_REQUEST_COLS} FROM sdai_signature_requests r
                    LEFT JOIN sdai_users rb ON rb.id = r.requested_by
                    WHERE r.tenant_id = CAST(:tid AS uuid) AND r.table_name = :t
                      AND r.document_id = CAST(:id AS uuid)
                    ORDER BY r.created_at DESC
                """),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
        return [await _request_out(conn, dict(r), current_user) for r in rows]


class SignerSpec(BaseModel):
    user_id: str
    title: str | None = Field(None, max_length=100)


class CreateRequestBody(BaseModel):
    signers: list[SignerSpec] = Field(..., min_length=1, max_length=MAX_SIGNERS)
    reason: str = Field(..., min_length=2, max_length=200)
    message: str | None = Field(None, max_length=2000)


@router.post(
    "/documents/{table_name}/{doc_id}/signature-requests",
    summary="Ask people to sign a document",
    status_code=201,
)
@limiter.limit("30/minute")
async def create_request(
    request: Request,
    table_name: str,
    doc_id: str,
    body: CreateRequestBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    locale = current_user.locale
    ids = [_uuid(s.user_id) for s in body.signers]
    if None in ids or len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail=t("signatures.bad_signers", locale))
    async with _engine().begin() as conn:
        safe, _ = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        allowed = set(
            await notifications.users_with_permission(
                conn, current_user.tenant_id, safe, doc_id, "read", user_ids=ids
            )
        )
        if allowed != set(ids):
            raise HTTPException(
                status_code=422, detail=t("signatures.bad_signers", locale)
            )
        pending = (
            await conn.execute(
                text("""
                    SELECT 1 FROM sdai_signature_requests
                    WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                      AND document_id = CAST(:id AS uuid) AND status = 'pending'
                """),
                {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
            )
        ).first()
        if pending:
            raise HTTPException(
                status_code=409, detail=t("signatures.already_pending", locale)
            )
        signers = [
            {
                "user_id": user_id,
                "order": n,
                "title": (spec.title or "").strip() or None,
                "status": "pending",
                "signed_at": None,
                "signature_id": None,
            }
            for n, (user_id, spec) in enumerate(zip(ids, body.signers), 1)
        ]
        request_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_signature_requests
                        (tenant_id, table_name, document_id, requested_by, reason, message, signers)
                    VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), CAST(:by AS uuid), :r, :m,
                            CAST(:s AS jsonb))
                    RETURNING id::text
                """),
                {
                    "tid": current_user.tenant_id,
                    "t": safe,
                    "id": doc_id,
                    "by": current_user.id,
                    "r": body.reason.strip(),
                    "m": body.message,
                    "s": json.dumps(signers),
                },
            )
        ).scalar()
        record_id = await _record_id(conn, safe, doc_id)
        await notifications.notify(
            conn,
            current_user.tenant_id,
            [ids[0]],
            "signature_requested",
            {
                "record_id": record_id,
                "by": current_user.full_name or current_user.email,
                "request_id": request_id,
            },
            table_name=safe,
            document_id=doc_id,
        )
        row = await _load_request(conn, request_id, current_user)
        out = await _request_out(conn, row, current_user)
    await log_action(
        action="signature_requested",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "request_id": request_id,
            "signers": ids,
            "reason": body.reason.strip(),
        },
        ip_address=client_ip(request),
    )
    return out


async def _record_id(conn, table: str, doc_id: str):
    return (
        await conn.execute(
            text(f'SELECT record_id FROM "{table}" WHERE id = CAST(:id AS uuid)'),
            {"id": doc_id},
        )
    ).scalar()


def _check_pending_signer(
    row: dict, current_user: CurrentUser
) -> tuple[list[dict], dict]:
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=t("signatures.request_closed", current_user.locale)
        )
    signers = _json(row["signers"])
    nxt = _next_signer(signers)
    if nxt is None or nxt["user_id"] != current_user.id:
        raise HTTPException(
            status_code=403, detail=t("signatures.not_your_turn", current_user.locale)
        )
    return signers, nxt


# ── Signing ───────────────────────────────────────────────────────────────────


class SignBody(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)
    reason: str | None = Field(None, max_length=200)


async def _check_password(current_user: CurrentUser, password: str) -> bool:
    async with _engine().connect() as conn:
        hashed = (
            await conn.execute(
                text(
                    "SELECT hashed_password FROM sdai_users WHERE id = CAST(:u AS uuid)"
                ),
                {"u": current_user.id},
            )
        ).scalar()
    if not hashed:
        return False
    return await asyncio.to_thread(bcrypt.checkpw, password.encode(), hashed.encode())


def _write_new(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "xb") as fh:  # never overwrite an existing file
        fh.write(data)


@router.post(
    "/signature-requests/{request_id}/sign", summary="Sign (re-enter your password)"
)
@limiter.limit("10/minute")
async def sign(
    request: Request,
    request_id: str,
    body: SignBody,
    current_user: CurrentUser = Depends(require_user),
):
    locale = current_user.locale
    async with _engine().connect() as conn:
        row = await _load_request(conn, request_id, current_user)
    _check_pending_signer(row, current_user)
    if not await _check_password(current_user, body.password):
        await log_action(
            action="signature_password_failed",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=row["table_name"],
            document_id=row["document_id"],
            details={"request_id": row["id"]},
            ip_address=client_ip(request),
        )
        raise HTTPException(
            status_code=403, detail=t("signatures.wrong_password", locale)
        )
    try:
        cert = signatures.signer_info()
    except signatures.SigningUnavailable:
        raise HTTPException(
            status_code=503, detail=t("signatures.not_configured", locale)
        ) from None

    reason = (body.reason or "").strip() or row["reason"]
    signed_at = datetime.now(timezone.utc)
    written: list[Path] = []
    try:
        async with _engine().begin() as conn:
            row = await _load_request(conn, request_id, current_user, lock=True)
            signers, me = _check_pending_signer(row, current_user)
            safe, cols = await _table_or_404(conn, row["table_name"], current_user)
            doc_id = row["document_id"]
            # Version-change rules: check-out lock (409) and lifecycle read-only.
            next_no = await _lock_for_change(conn, safe, doc_id, current_user)
            f = await document_files.resolve(
                conn, safe, cols, doc_id, current_user.tenant_id
            )
            if f is None:
                raise HTTPException(
                    status_code=404, detail=t("common.document_not_found", locale)
                )
            path = await document_files.check_servable(conn, f, locale)
            source = await asyncio.to_thread(path.read_bytes)
            source_no, source_hash = f.version_no, hashlib.sha256(source).hexdigest()
            dest_dir = ingest_router.VERSIONS_DIR / current_user.tenant_slug / doc_id
            stem = Path(f.original_filename or path.name).stem[:80] or "document"
            converted = None
            if not signatures.is_pdf(f.file_path, f.mime):
                if not signatures.is_image(f.file_path, f.mime):
                    raise HTTPException(
                        status_code=422, detail=t("signatures.unsupported_file", locale)
                    )
                pdf, is_pdfa = await asyncio.to_thread(signatures.to_pdf, path)
                dest = dest_dir / f"v{next_no}_{stem}.pdf"
                await asyncio.to_thread(_write_new, dest, pdf)
                written.append(dest)
                converted = await _insert_version(
                    conn,
                    safe,
                    doc_id,
                    current_user,
                    version_no=next_no,
                    file_path=str(dest),
                    file_hash=hashlib.sha256(pdf).hexdigest(),
                    size=len(pdf),
                    mime="application/pdf",
                    original_filename=f"{stem}.pdf",
                    comment=t(
                        "signatures.converted_pdfa"
                        if is_pdfa
                        else "signatures.converted_pdf",
                        locale,
                        n=source_no,
                    ),
                    restored_from=None,
                )
                source, source_no = pdf, next_no
                source_hash = converted["file_hash"]
                next_no += 1
            signer_name = current_user.full_name or current_user.email
            title = me.get("title") or _ROLE_TITLES.get(current_user.role, {}).get(
                locale, current_user.role
            )
            field_count = signatures.existing_signatures(source)
            try:
                signed = await asyncio.to_thread(
                    signatures.sign_pdf,
                    source,
                    signer_name=signer_name,
                    role=title,
                    reason=reason,
                    signed_at=signed_at,
                    locale=locale,
                )
            except signatures.SigningError:
                raise HTTPException(
                    status_code=422, detail=t("signatures.cannot_sign", locale)
                ) from None
            signed_hash = hashlib.sha256(signed).hexdigest()
            dest = dest_dir / f"v{next_no}_{stem}_signe.pdf"
            await asyncio.to_thread(_write_new, dest, signed)
            written.append(dest)
            version = await _insert_version(
                conn,
                safe,
                doc_id,
                current_user,
                version_no=next_no,
                file_path=str(dest),
                file_hash=signed_hash,
                size=len(signed),
                mime="application/pdf",
                original_filename=f"{stem}_signe.pdf",
                comment=t("signatures.signed_version", locale, name=signer_name),
                restored_from=None,
            )
            signature_id = (
                await conn.execute(
                    text("""
                        INSERT INTO sdai_signatures
                            (tenant_id, table_name, document_id, request_id, version_no,
                             source_version_no, field_name, signer_id, signer_name, signer_title,
                             reason, signed_at, ip_address, cert_subject, cert_fingerprint,
                             source_file_hash, signed_file_hash)
                        VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), CAST(:rid AS uuid), :v,
                                :sv, :field, CAST(:u AS uuid), :name, :title, :reason, :at, :ip,
                                :subject, :fp, :sh, :h)
                        RETURNING id::text
                    """),
                    {
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                        "rid": row["id"],
                        "v": next_no,
                        "sv": source_no,
                        "field": f"SDAI_Signature_{field_count + 1}",
                        "u": current_user.id,
                        "name": signer_name,
                        "title": title,
                        "reason": reason,
                        "at": signed_at,
                        "ip": client_ip(request),
                        "subject": cert.subject,
                        "fp": cert.fingerprint,
                        "sh": source_hash,
                        "h": signed_hash,
                    },
                )
            ).scalar()
            me.update(
                {
                    "status": "signed",
                    "signed_at": signed_at.isoformat(),
                    "signature_id": signature_id,
                }
            )
            nxt = _next_signer(signers)
            done = nxt is None
            await conn.execute(
                text("""
                    UPDATE sdai_signature_requests
                    SET signers = CAST(:s AS jsonb), status = :st,
                        closed_at = CASE WHEN :done THEN now() ELSE NULL END
                    WHERE id = CAST(:id AS uuid)
                """),
                {
                    "s": json.dumps(signers),
                    "st": "completed" if done else "pending",
                    "done": done,
                    "id": row["id"],
                },
            )
            record_id = await _record_id(conn, safe, doc_id)
            payload = {
                "record_id": record_id,
                "by": signer_name,
                "request_id": row["id"],
            }
            if done:
                await notifications.notify(
                    conn,
                    current_user.tenant_id,
                    [row["requested_by"]],
                    "signature_completed",
                    payload,
                    table_name=safe,
                    document_id=doc_id,
                    exclude=current_user.id,
                )
            else:
                await notifications.notify(
                    conn,
                    current_user.tenant_id,
                    [nxt["user_id"]],
                    "signature_requested",
                    {
                        **payload,
                        "by": row["requested_by_name"] or row["requested_by_email"],
                    },
                    table_name=safe,
                    document_id=doc_id,
                )
    except BaseException:
        for p in written:  # the transaction rolled back - no orphan files
            p.unlink(missing_ok=True)
        raise

    await log_action(
        action="document_signed",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "request_id": row["id"],
            "signature_id": signature_id,
            "version_no": next_no,
            "source_version_no": source_no,
            "converted_version_no": converted["version_no"] if converted else None,
            "signed_file_hash": signed_hash,
            "cert_fingerprint": cert.fingerprint,
            "reason": reason,
        },
        ip_address=client_ip(request),
    )
    await invalidate_cache("review")
    async with _engine().connect() as conn:
        out = await _request_out(
            conn, await _load_request(conn, request_id, current_user), current_user
        )
    return {"request": out, "version": version, "signature_id": signature_id}


class NoteBody(BaseModel):
    note: str | None = Field(None, max_length=1000)


@router.post("/signature-requests/{request_id}/decline", summary="Decline to sign")
async def decline(
    request: Request,
    request_id: str,
    body: NoteBody | None = None,
    current_user: CurrentUser = Depends(require_user),
):
    note = (body.note if body else None) or None
    async with _engine().begin() as conn:
        row = await _load_request(conn, request_id, current_user, lock=True)
        signers, me = _check_pending_signer(row, current_user)
        me.update({"status": "declined", "signed_at": None})
        await conn.execute(
            text("""
                UPDATE sdai_signature_requests
                SET signers = CAST(:s AS jsonb), status = 'declined', closed_at = now(),
                    closed_by = CAST(:u AS uuid), close_note = :n
                WHERE id = CAST(:id AS uuid)
            """),
            {
                "s": json.dumps(signers),
                "u": current_user.id,
                "n": note,
                "id": row["id"],
            },
        )
        record_id = await _record_id(conn, row["table_name"], row["document_id"])
        await notifications.notify(
            conn,
            current_user.tenant_id,
            [row["requested_by"]],
            "signature_declined",
            {
                "record_id": record_id,
                "by": current_user.full_name or current_user.email,
                "request_id": row["id"],
            },
            table_name=row["table_name"],
            document_id=row["document_id"],
            exclude=current_user.id,
        )
        out = await _request_out(
            conn, await _load_request(conn, request_id, current_user), current_user
        )
    await log_action(
        action="signature_declined",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=row["table_name"],
        document_id=row["document_id"],
        details={"request_id": row["id"], "note": note},
        ip_address=client_ip(request),
    )
    return out


@router.post(
    "/signature-requests/{request_id}/cancel", summary="Cancel a signature request"
)
async def cancel(
    request: Request,
    request_id: str,
    body: NoteBody | None = None,
    current_user: CurrentUser = Depends(require_reviewer),
):
    note = (body.note if body else None) or None
    async with _engine().begin() as conn:
        row = await _load_request(conn, request_id, current_user, lock=True)
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=t("signatures.request_closed", current_user.locale),
            )
        if row["requested_by"] != current_user.id and current_user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail=t("signatures.not_requester", current_user.locale),
            )
        await conn.execute(
            text("""
                UPDATE sdai_signature_requests
                SET status = 'cancelled', closed_at = now(), closed_by = CAST(:u AS uuid),
                    close_note = :n
                WHERE id = CAST(:id AS uuid)
            """),
            {"u": current_user.id, "n": note, "id": row["id"]},
        )
        out = await _request_out(
            conn, await _load_request(conn, request_id, current_user), current_user
        )
    await log_action(
        action="signature_request_cancelled",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=row["table_name"],
        document_id=row["document_id"],
        details={"request_id": row["id"], "note": note},
        ip_address=client_ip(request),
    )
    return out


# ── Verification ──────────────────────────────────────────────────────────────


@router.get(
    "/documents/{table_name}/{doc_id}/signatures",
    summary="Signatures of a document: evidence and PAdES verification",
)
@limiter.limit("30/minute")
async def verify(
    request: Request,
    table_name: str,
    doc_id: str,
    version: int | None = Query(None, ge=1),
    current_user: CurrentUser = Depends(require_user),
):
    locale = current_user.locale
    async with _engine().connect() as conn:
        safe, cols = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        evidence = [
            {k: _iso(v) for k, v in dict(r).items()}
            for r in (
                await conn.execute(
                    text("""
                        SELECT id::text AS id, request_id::text AS request_id, version_no,
                               source_version_no, field_name, signer_id::text AS signer_id,
                               signer_name, signer_title, reason, level, signed_at, ip_address,
                               cert_subject, cert_fingerprint, source_file_hash, signed_file_hash
                        FROM sdai_signatures
                        WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                          AND document_id = CAST(:id AS uuid)
                        ORDER BY signed_at
                    """),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            ).mappings()
        ]
        audit = [
            {k: _iso(v) for k, v in dict(r).items()}
            for r in (
                await conn.execute(
                    text("""
                        SELECT action, user_email, created_at, details
                        FROM sdai_audit_log
                        WHERE table_name = :t AND document_id = CAST(:id AS uuid)
                          AND action = ANY(:actions)
                        ORDER BY created_at
                    """),
                    {"t": safe, "id": doc_id, "actions": list(_EVIDENCE_ACTIONS)},
                )
            ).mappings()
        ]
        f = await document_files.resolve(
            conn, safe, cols, doc_id, current_user.tenant_id, version
        )
        verification = None
        if f is not None and signatures.is_pdf(f.file_path, f.mime):
            path = await document_files.check_servable(conn, f, locale)
            data = await asyncio.to_thread(path.read_bytes)
            file_hash = hashlib.sha256(data).hexdigest()
            try:
                embedded = await asyncio.to_thread(signatures.verify_pdf, data)
            except signatures.SigningError:
                embedded = []
            by_field = {
                e["field_name"]: e for e in evidence if e["version_no"] <= f.version_no
            }
            for sig in embedded:
                ev = by_field.get(sig["field"])
                sig["evidence_id"] = ev["id"] if ev else None
                sig["evidence_matches"] = bool(
                    ev and ev["cert_fingerprint"] == sig.get("cert_fingerprint")
                )
            verification = {
                "version_no": f.version_no,
                "file_hash": file_hash,
                "file_hash_ok": f.file_hash is None or f.file_hash == file_hash,
                "signatures": embedded,
                "all_ok": bool(embedded) and all(s["ok"] for s in embedded),
            }
        elif f is not None:
            verification = {
                "version_no": f.version_no,
                "signatures": [],
                "all_ok": False,
                "not_pdf": True,
            }
    return {
        "level": "simple",
        "evidence": evidence,
        "verification": verification,
        "audit": audit,
    }
