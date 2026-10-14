"""
External share links for one filed document (GED Phase 5).

Authenticated (the person sharing):
  POST   /api/db/documents/{tbl}/{id}/shares              - create a link (shown ONCE)
  GET    /api/db/documents/{tbl}/{id}/shares              - links on the document
  DELETE /api/db/documents/{tbl}/{id}/shares/{share_id}   - revoke a link

Public - the only routes WITHOUT get_current_user (see CLAUDE.md):
  GET  /s/{token}           - minimal landing page (file name, expiry, password form)
  POST /s/{token}/download  - the file (password checked here)

Rules:
  - creating needs can_share (CurrentUser.can_share_documents) AND the
    download permission on the document; `secret` documents are never
    shared, `confidential` ones only by admins.
  - only sha256(token) is stored - a 32-byte random token, in the URL only.
  - every public use re-checks everything: link not revoked/expired/used up,
    document still live, and its creator still active and still allowed to
    hand the document out (a link dies with its creator's access).
  - any failure is the same generic 404 page - an outsider can't tell an
    expired link from a mistyped one. Strict per-IP rate limits.
  - audit: document_shared, share_link_revoked, share_link_accessed (with
    the visitor's IP, event viewed/downloaded/password_failed).
"""

import asyncio
import hashlib
import hmac
import html
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import document_files
from audit import client_ip, log_action
from auth import CurrentUser, load_user, require_user
from cache import first_in_window
from documents_router import (
    _document_visible,
    _get_tables_columns,
    _require_document_permission,
    _sanitize,
)
from export_format import serial
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from i18n import format_datetime, resolve_locale, t
from ingest_router import _engine
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text

router = APIRouter(prefix="/api/db", tags=["shares"])
public_router = APIRouter(prefix="/s", tags=["shares (public)"])

# Base of the links handed out - the internet-facing (cloud) node. Without
# it, the address the request came in on.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

DEFAULT_EXPIRY_DAYS = 7
MAX_EXPIRY_DAYS = 30
MAX_DOWNLOADS_CAP = 1000

# secrets.token_urlsafe(32): 43 URL-safe base64 characters.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _share_status(row) -> str:
    now = datetime.now(timezone.utc)
    if row["revoked_at"] is not None:
        return "revoked"
    if row["expires_at"] <= now:
        return "expired"
    if (
        row["max_downloads"] is not None
        and row["download_count"] >= row["max_downloads"]
    ):
        return "exhausted"
    return "active"


_SHARE_COLS = """
    s.id, s.version_no, s.expires_at, s.max_downloads, s.note, s.created_by,
    u.email AS created_by_email, s.created_at, s.revoked_at, s.revoked_by,
    (s.password_hash IS NOT NULL) AS has_password,
    (SELECT count(*) FROM sdai_share_downloads sd WHERE sd.share_id = s.id) AS download_count,
    (SELECT max(downloaded_at) FROM sdai_share_downloads sd WHERE sd.share_id = s.id)
        AS last_downloaded_at
"""


def _serialize_share(row) -> dict:
    data = {k: serial(v) for k, v in dict(row).items()}
    data["status"] = _share_status(row)
    return data


async def _table_or_404(
    conn, table_name: str, current_user: CurrentUser
) -> tuple[str, dict]:
    safe = _sanitize(table_name)
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    if safe not in tables_cols:
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe, tables_cols[safe]


async def _confidentiality(conn, table: str, doc_id: str) -> str:
    return (
        await conn.execute(
            text(f'SELECT confidentiality FROM "{table}" WHERE id = CAST(:id AS uuid)'),
            {"id": doc_id},
        )
    ).scalar() or "internal"


def _may_share_level(user: CurrentUser, confidentiality: str) -> bool:
    if confidentiality == "secret":
        return False
    if confidentiality == "confidential":
        return user.role == "admin"
    return True


# ── Authenticated: create / list / revoke ─────────────────────────────────────


class CreateShareBody(BaseModel):
    expires_in_days: int = Field(DEFAULT_EXPIRY_DAYS, ge=1, le=MAX_EXPIRY_DAYS)
    password: str | None = Field(None, min_length=4, max_length=128)
    max_downloads: int | None = Field(None, ge=1, le=MAX_DOWNLOADS_CAP)
    version_no: int | None = Field(
        None, ge=1, description="Pin a version - default: always the current one"
    )
    note: str | None = Field(None, max_length=200, description="Who the link is for")


@router.post(
    "/documents/{table_name}/{doc_id}/shares", summary="Create an external share link"
)
@limiter.limit("20/minute")
async def create_share(
    request: Request,
    table_name: str,
    doc_id: str,
    body: CreateShareBody,
    current_user: CurrentUser = Depends(require_user),
):
    """A new link to download this document from outside, without an
    account. The response carries the full URL - the only time it is ever
    available (only its hash is stored)."""
    locale = current_user.locale
    if not current_user.can_share_documents:
        raise HTTPException(
            status_code=403, detail=t("shares.permission_required", locale)
        )
    token = secrets.token_urlsafe(32)
    password_hash = (
        (
            await asyncio.to_thread(
                bcrypt.hashpw, body.password.encode(), bcrypt.gensalt()
            )
        ).decode()
        if body.password
        else None
    )
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    async with _engine().begin() as conn:
        safe, cols = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "download")
        level = await _confidentiality(conn, safe, doc_id)
        if not _may_share_level(current_user, level):
            raise HTTPException(
                status_code=403, detail=t(f"shares.forbidden_{level}", locale)
            )
        f = await document_files.resolve(
            conn, safe, cols, doc_id, current_user.tenant_id, body.version_no
        )
        if f is None:
            raise HTTPException(status_code=404, detail=t("versions.not_found", locale))
        share_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_share_links
                        (tenant_id, table_name, document_id, token_hash, version_no, password_hash,
                         expires_at, max_downloads, note, created_by)
                    VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), :h, :v, :pw, :exp, :max,
                            :note, CAST(:by AS uuid))
                    RETURNING id::text
                """),
                {
                    "tid": current_user.tenant_id,
                    "t": safe,
                    "id": doc_id,
                    "h": _hash_token(token),
                    "v": body.version_no,
                    "pw": password_hash,
                    "exp": expires_at,
                    "max": body.max_downloads,
                    "note": body.note,
                    "by": current_user.id,
                },
            )
        ).scalar()
        row = (
            (
                await conn.execute(
                    text(f"""
                        SELECT {_SHARE_COLS} FROM sdai_share_links s
                        LEFT JOIN sdai_users u ON u.id = s.created_by
                        WHERE s.id = CAST(:sid AS uuid)
                    """),
                    {"sid": share_id},
                )
            )
            .mappings()
            .one()
        )

    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    await log_action(
        action="document_shared",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "share_id": share_id,
            "expires_at": expires_at.isoformat(),
            "max_downloads": body.max_downloads,
            "password": password_hash is not None,
            "version_no": body.version_no,
        },
        ip_address=client_ip(request),
    )
    return {"share": _serialize_share(row), "url": f"{base}/s/{token}"}


@router.get(
    "/documents/{table_name}/{doc_id}/shares", summary="Share links on a document"
)
async def list_shares(
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Admins and access managers see every link on the document; anyone
    else who may share it sees the links they created."""
    async with _engine().connect() as conn:
        safe, _ = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        sees_all = current_user.can_manage_document_access
        if not sees_all and not current_user.can_share_documents:
            return {"shares": []}
        rows = (
            (
                await conn.execute(
                    text(f"""
                        SELECT {_SHARE_COLS} FROM sdai_share_links s
                        LEFT JOIN sdai_users u ON u.id = s.created_by
                        WHERE s.tenant_id = CAST(:tid AS uuid) AND s.table_name = :t
                          AND s.document_id = CAST(:id AS uuid)
                          {"" if sees_all else "AND s.created_by = CAST(:me AS uuid)"}
                        ORDER BY s.created_at DESC
                    """),
                    {
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                        "me": current_user.id,
                    },
                )
            )
            .mappings()
            .all()
        )
    return {"shares": [_serialize_share(r) for r in rows]}


@router.delete(
    "/documents/{table_name}/{doc_id}/shares/{share_id}", summary="Revoke a share link"
)
@limiter.limit("30/minute")
async def revoke_share(
    request: Request,
    table_name: str,
    doc_id: str,
    share_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Its creator, an admin or an access manager can revoke a link - at
    once, on this node (and on the other one as soon as it syncs)."""
    locale = current_user.locale
    try:
        share_uuid = str(uuid.UUID(share_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=t("shares.not_found", locale)
        ) from exc
    async with _engine().begin() as conn:
        safe, _ = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        row = (
            (
                await conn.execute(
                    text("""
                        SELECT created_by::text AS created_by, revoked_at FROM sdai_share_links
                        WHERE id = CAST(:sid AS uuid) AND tenant_id = CAST(:tid AS uuid)
                          AND table_name = :t AND document_id = CAST(:id AS uuid)
                        FOR UPDATE
                    """),
                    {
                        "sid": share_uuid,
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or (
            row["created_by"] != current_user.id
            and not current_user.can_manage_document_access
        ):
            raise HTTPException(status_code=404, detail=t("shares.not_found", locale))
        if row["revoked_at"] is None:
            await conn.execute(
                text(
                    "UPDATE sdai_share_links SET revoked_at = now(), revoked_by = CAST(:by AS uuid)"
                    " WHERE id = CAST(:sid AS uuid)"
                ),
                {"sid": share_uuid, "by": current_user.id},
            )
    if row["revoked_at"] is None:
        await log_action(
            action="share_link_revoked",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=safe,
            document_id=doc_id,
            details={"share_id": share_uuid},
            ip_address=client_ip(request),
        )
    return {"revoked": True}


# ── Public ────────────────────────────────────────────────────────────────────


async def _usable_share(conn, token: str):
    """(share row, DocumentFile, servable path) for a token that may be
    used right now, else None - every reason looks the same from outside."""
    if not _TOKEN_RE.match(token or ""):
        return None
    token_hash = _hash_token(token)
    row = (
        (
            await conn.execute(
                text("""
                    SELECT s.id::text AS id, s.tenant_id::text AS tenant_id, s.table_name,
                           s.document_id::text AS document_id, s.token_hash, s.version_no,
                           s.password_hash, s.expires_at, s.max_downloads, s.revoked_at,
                           s.created_by::text AS created_by, t.slug AS tenant_slug,
                           (SELECT count(*) FROM sdai_share_downloads sd
                            WHERE sd.share_id = s.id) AS download_count
                    FROM sdai_share_links s
                    JOIN sdai_tenants t ON t.id = s.tenant_id
                    WHERE s.token_hash = :h
                """),
                {"h": token_hash},
            )
        )
        .mappings()
        .one_or_none()
    )
    # The lookup is by hash (an index probe reveals nothing about the
    # token); compare_digest keeps the final check constant-time anyway.
    if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
        return None
    if _share_status(row) != "active":
        return None
    creator = await load_user(conn, row["created_by"])
    if (
        creator is None
        or creator.tenant_id != row["tenant_id"]
        or not creator.can_share_documents
    ):
        return None
    tables_cols = await _get_tables_columns(conn, row["tenant_slug"])
    table = row["table_name"]
    if table not in tables_cols:
        return None
    # Still live, still within the creator's clearance and grants.
    if not await _document_visible(
        conn, table, row["document_id"], creator, "download"
    ):
        return None
    if not _may_share_level(
        creator, await _confidentiality(conn, table, row["document_id"])
    ):
        return None
    f = await document_files.resolve(
        conn,
        table,
        tables_cols[table],
        row["document_id"],
        row["tenant_id"],
        row["version_no"],
    )
    if f is None:
        return None
    return row, f


def _page(locale: str, body: str, status: int = 200) -> HTMLResponse:
    """A self-contained page - no app bundle, no script, no external asset."""
    title = html.escape(t("shares.page_title", locale))
    doc = f"""<!doctype html>
<html lang="{locale}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --fg:#1d232b; --muted:#5f6b7a;
  --line:#d8dee6; --accent:#1f5fbf; --err:#b42318; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14181d; --card:#1d232b; --fg:#e8edf3;
  --muted:#9aa7b6; --line:#323b46; --accent:#6ea2ff; --err:#ff8a7a; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
  padding:16px; background:var(--bg); color:var(--fg);
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ width:100%; max-width:440px; background:var(--card); border:1px solid var(--line);
  border-radius:10px; padding:24px; }}
h1 {{ font-size:18px; margin:0 0 4px; overflow-wrap:anywhere; }}
p {{ margin:4px 0; color:var(--muted); }}
dl {{ margin:16px 0; display:grid; grid-template-columns:auto 1fr; gap:4px 12px; }}
dt {{ color:var(--muted); }} dd {{ margin:0; }}
label {{ display:block; margin:16px 0 4px; font-weight:600; }}
input {{ width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:6px;
  background:transparent; color:inherit; font:inherit; }}
button {{ margin-top:16px; width:100%; padding:10px; border:0; border-radius:6px;
  background:var(--accent); color:#fff; font:inherit; font-weight:600; cursor:pointer; }}
.err {{ color:var(--err); }}
footer {{ margin-top:16px; font-size:12px; color:var(--muted); }}
</style></head>
<body><main>{body}<footer>{html.escape(t("shares.page_footer", locale))}</footer></main></body></html>"""
    return HTMLResponse(
        doc,
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


def _not_found_page(locale: str) -> HTMLResponse:
    return _page(
        locale,
        f"<h1>{html.escape(t('shares.invalid_title', locale))}</h1>"
        f"<p>{html.escape(t('shares.invalid_body', locale))}</p>",
        status=404,
    )


def _landing_page(
    locale: str, token: str, row, f, error: str | None = None, status: int = 200
):
    e = html.escape
    name = document_files.download_name(f.record_id, f.original_filename, "document")
    expires = format_datetime(row["expires_at"], locale)
    facts = [(t("shares.page_expires", locale), expires)]
    if f.size:
        facts.append((t("shares.page_size", locale), f"{f.size / 1024:.0f} KB"))
    if row["max_downloads"] is not None:
        left = row["max_downloads"] - row["download_count"]
        facts.append((t("shares.page_downloads_left", locale), str(left)))
    dl = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in facts)
    password = (
        f'<label for="pw">{e(t("shares.page_password", locale))}</label>'
        '<input id="pw" name="password" type="password" required autocomplete="off">'
        if row["password_hash"]
        else ""
    )
    err = f'<p class="err" role="alert">{e(error)}</p>' if error else ""
    body = (
        f"<p>{e(t('shares.page_intro', locale))}</p><h1>{e(name)}</h1><dl>{dl}</dl>"
        f'<form method="post" action="/s/{e(token)}/download">{password}{err}'
        f'<button type="submit">{e(t("shares.page_download", locale))}</button></form>'
    )
    return _page(locale, body, status)


async def _log_access(
    request: Request, row, event: str, version_no: int | None = None
) -> None:
    await log_action(
        action="share_link_accessed",
        table_name=row["table_name"],
        document_id=row["document_id"],
        details={
            "share_id": row["id"],
            "event": event,
            **({"version_no": version_no} if version_no is not None else {}),
        },
        ip_address=client_ip(request),
    )


@public_router.get("/{token}", include_in_schema=False)
@limiter.limit("30/minute")
async def share_landing(
    request: Request, token: str, locale: str = Depends(resolve_locale)
):
    if not _TOKEN_RE.match(token):
        return _not_found_page(locale)
    async with _engine().connect() as conn:
        found = await _usable_share(conn, token)
    if found is None:
        return _not_found_page(locale)
    row, f = found
    ip = client_ip(request) or "-"
    if await first_in_window(f"share-view:{row['id']}:{ip}", 300):
        await _log_access(request, row, "viewed")
    return _landing_page(locale, token, row, f)


@public_router.post("/{token}/download", include_in_schema=False)
@limiter.limit("10/minute")
async def share_download(
    request: Request,
    token: str,
    password: str | None = Form(None, max_length=128),
    locale: str = Depends(resolve_locale),
):
    if not _TOKEN_RE.match(token):
        return _not_found_page(locale)
    async with _engine().begin() as conn:
        found = await _usable_share(conn, token)
        if found is None:
            return _not_found_page(locale)
        row, f = found
        if row["password_hash"]:
            ok = bool(password) and await asyncio.to_thread(
                bcrypt.checkpw, password.encode(), row["password_hash"].encode()
            )
            if not ok:
                await _log_access(request, row, "password_failed")
                return _landing_page(
                    locale,
                    token,
                    row,
                    f,
                    t("shares.page_wrong_password", locale),
                    status=401,
                )
        try:
            path = await document_files.check_servable(
                conn, f, locale, pending_status=404
            )
        except HTTPException:
            return _not_found_page(locale)
        # Serialize uses of this link so max_downloads can't be overrun by
        # parallel requests on this node, then count this download.
        await conn.execute(
            text(
                "SELECT 1 FROM sdai_share_links WHERE id = CAST(:sid AS uuid) FOR UPDATE"
            ),
            {"sid": row["id"]},
        )
        if row["max_downloads"] is not None:
            used = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM sdai_share_downloads WHERE share_id = CAST(:sid AS uuid)"
                    ),
                    {"sid": row["id"]},
                )
            ).scalar()
            if used >= row["max_downloads"]:
                return _not_found_page(locale)
        # Built before counting: a PDF that can't be watermarked must not
        # use up a download (the transaction rolls back).
        filename = document_files.download_name(
            f.record_id, f.original_filename, path.name
        )
        try:
            response, _ = await document_files.deliver(
                f,
                path,
                filename,
                inline=False,
                who=t("shares.watermark_who", locale, id=row["id"][:8]),
                locale=locale,
            )
        except HTTPException:
            return _not_found_page(locale)
        await conn.execute(
            text(
                "INSERT INTO sdai_share_downloads (share_id, version_no, ip_address)"
                " VALUES (CAST(:sid AS uuid), :v, :ip)"
            ),
            {"sid": row["id"], "v": f.version_no, "ip": client_ip(request)},
        )

    await _log_access(request, row, "downloaded", f.version_no)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
