"""
Comments on a filed document (GED Phase 7.3).

Endpoints:
  GET    /api/db/documents/{tbl}/{id}/comments               every comment, oldest first (read)
  POST   /api/db/documents/{tbl}/{id}/comments               {body, parent_id?, mentions?}
  GET    /api/db/documents/{tbl}/{id}/comments/mentionable   people who can read it (?q=)
  PATCH  /api/db/comments/{comment_id}                       {body, mentions?} - the author
  DELETE /api/db/comments/{comment_id}                       the author or an admin (soft)

Posting needs read access to the document and the reviewer role (admins
and reviewers: whoever can hold `edit` on a document) - readers only read.
A checked-out or archived document can still be commented: a comment
never changes the record.

@mentions: the client sends the mentioned user ids (picked from
/mentionable); only people who can read the document are kept, and each is
notified once ("mention"). A reply also notifies the author of the comment
it answers ("comment_reply"). Notifications and e-mails never carry the
comment text.

Audit: comment_added, comment_edited, comment_deleted (ids, lengths and
mentions - never the text, which lives in sdai_comments).
"""

import json
import uuid

import notifications
from audit import client_ip, log_action
from auth import CurrentUser, require_reviewer, require_user
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

router = APIRouter(prefix="/api/db", tags=["comments"])

MAX_MENTIONS = 20


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _uuid(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


async def _table_or_404(conn, table_name: str, current_user: CurrentUser) -> str:
    safe = _sanitize(table_name)
    if safe not in await _get_tables_columns(conn, current_user.tenant_slug):
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    return safe


def _out(row: dict, current_user: CurrentUser) -> dict:
    mentions = row["mentions"]
    if isinstance(mentions, str):
        mentions = json.loads(mentions)
    deleted = row["deleted_at"] is not None
    mine = row["author_id"] == current_user.id
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "version_no": row["version_no"],
        "author_id": row["author_id"],
        "author_name": row["author_name"] or row["author_email"],
        "body": None if deleted else row["body"],
        "mentions": [] if deleted else row["mention_users"] or [],
        "created_at": _iso(row["created_at"]),
        "edited_at": _iso(row["edited_at"]),
        "deleted": deleted,
        "can_edit": mine and not deleted,
        "can_delete": (mine or current_user.role == "admin") and not deleted,
    }


_SELECT = """
    SELECT c.id::text AS id, c.parent_id::text AS parent_id, c.version_no,
           c.author_id::text AS author_id, u.full_name AS author_name, u.email AS author_email,
           c.body, c.mentions, c.created_at, c.edited_at, c.deleted_at,
           (SELECT json_agg(json_build_object('id', m.id::text,
                                              'name', COALESCE(m.full_name, m.email)))
            FROM sdai_users m
            WHERE m.id::text IN (SELECT jsonb_array_elements_text(c.mentions))) AS mention_users
    FROM sdai_comments c
    LEFT JOIN sdai_users u ON u.id = c.author_id
"""


async def _load(conn, comment_id: str, current_user: CurrentUser) -> dict:
    """One comment of the caller's tenant on a document they can read."""
    cid = _uuid(comment_id)
    row = None
    if cid:
        row = (
            (
                await conn.execute(
                    text(
                        _SELECT
                        + " WHERE c.id = CAST(:id AS uuid) AND c.tenant_id = CAST(:tid AS uuid)"
                    ),
                    {"id": cid, "tid": current_user.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(
            status_code=404, detail=t("comments.not_found", current_user.locale)
        )
    doc = (
        await conn.execute(
            text(
                "SELECT table_name, document_id::text FROM sdai_comments WHERE id = CAST(:id AS uuid)"
            ),
            {"id": cid},
        )
    ).one()
    safe = await _table_or_404(conn, doc[0], current_user)
    await _require_document_permission(conn, safe, doc[1], current_user, "read")
    return {**dict(row), "table_name": safe, "document_id": doc[1]}


async def _valid_mentions(
    conn, current_user: CurrentUser, table: str, doc_id: str, mentions: list[str]
) -> list[str]:
    ids = [m for m in dict.fromkeys(_uuid(m) for m in mentions) if m][:MAX_MENTIONS]
    if not ids:
        return []
    return await notifications.users_with_permission(
        conn, current_user.tenant_id, table, doc_id, "read", user_ids=ids
    )


@router.get(
    "/documents/{table_name}/{doc_id}/comments", summary="Comments on a document"
)
async def list_comments(
    table_name: str, doc_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        rows = (
            (
                await conn.execute(
                    text(
                        _SELECT
                        + """ WHERE c.tenant_id = CAST(:tid AS uuid) AND c.table_name = :t
                              AND c.document_id = CAST(:id AS uuid)
                            ORDER BY c.created_at, c.id"""
                    ),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
    return [_out(dict(r), current_user) for r in rows]


@router.get(
    "/documents/{table_name}/{doc_id}/comments/mentionable",
    summary="People who can be mentioned on a document",
)
async def mentionable(
    table_name: str,
    doc_id: str,
    q: str = Query("", max_length=100),
    current_user: CurrentUser = Depends(require_reviewer),
):
    async with _engine().connect() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        like = f"%{q.strip().lower()}%"
        candidates = (
            (
                await conn.execute(
                    text("""
                    SELECT id::text FROM sdai_users
                    WHERE tenant_id = CAST(:tid AS uuid) AND is_active IS NOT FALSE
                      AND role IN ('admin', 'reviewer', 'reader') AND id <> CAST(:me AS uuid)
                      AND (lower(COALESCE(full_name, '')) LIKE :q OR lower(email) LIKE :q)
                    ORDER BY lower(COALESCE(full_name, email)) LIMIT 50
                """),
                    {"tid": current_user.tenant_id, "me": current_user.id, "q": like},
                )
            )
            .scalars()
            .all()
        )
        allowed = await notifications.users_with_permission(
            conn,
            current_user.tenant_id,
            safe,
            doc_id,
            "read",
            user_ids=list(candidates),
        )
        if not allowed:
            return []
        rows = await conn.execute(
            text("""
                SELECT id::text, full_name, email FROM sdai_users
                WHERE id = ANY(CAST(:ids AS uuid[]))
                ORDER BY lower(COALESCE(full_name, email)) LIMIT 10
            """),
            {"ids": allowed},
        )
        return [{"id": r[0], "name": r[1] or r[2], "email": r[2]} for r in rows]


class CommentBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)
    parent_id: str | None = None
    mentions: list[str] = Field(default_factory=list, max_length=MAX_MENTIONS)


@router.post(
    "/documents/{table_name}/{doc_id}/comments",
    summary="Comment on a document",
    status_code=201,
)
@limiter.limit("60/minute")
async def add_comment(
    request: Request,
    table_name: str,
    doc_id: str,
    body: CommentBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    text_body = body.body.strip()
    if not text_body:
        raise HTTPException(
            status_code=422, detail=t("comments.empty", current_user.locale)
        )
    async with _engine().begin() as conn:
        safe = await _table_or_404(conn, table_name, current_user)
        await _require_document_permission(conn, safe, doc_id, current_user, "read")
        parent_author = None
        parent_id = None
        if body.parent_id:
            parent = (
                await conn.execute(
                    text("""
                        SELECT id::text, author_id::text FROM sdai_comments
                        WHERE id = CAST(:p AS uuid) AND tenant_id = CAST(:tid AS uuid)
                          AND table_name = :t AND document_id = CAST(:id AS uuid)
                    """),
                    {
                        "p": _uuid(body.parent_id) or str(uuid.UUID(int=0)),
                        "tid": current_user.tenant_id,
                        "t": safe,
                        "id": doc_id,
                    },
                )
            ).one_or_none()
            if parent is None:
                raise HTTPException(
                    status_code=422,
                    detail=t("comments.bad_parent", current_user.locale),
                )
            parent_id, parent_author = parent
        mentions = await _valid_mentions(
            conn, current_user, safe, doc_id, body.mentions
        )
        version_no = (
            await conn.execute(
                text(
                    f'SELECT current_version FROM "{safe}" WHERE id = CAST(:id AS uuid)'
                ),
                {"id": doc_id},
            )
        ).scalar()
        comment_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_comments
                        (tenant_id, table_name, document_id, parent_id, version_no, author_id,
                         body, mentions)
                    VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), CAST(:p AS uuid), :v,
                            CAST(:a AS uuid), :b, CAST(:m AS jsonb))
                    RETURNING id::text
                """),
                {
                    "tid": current_user.tenant_id,
                    "t": safe,
                    "id": doc_id,
                    "p": parent_id,
                    "v": version_no,
                    "a": current_user.id,
                    "b": text_body,
                    "m": json.dumps(mentions),
                },
            )
        ).scalar()
        record_id = (
            await conn.execute(
                text(f'SELECT record_id FROM "{safe}" WHERE id = CAST(:id AS uuid)'),
                {"id": doc_id},
            )
        ).scalar()
        payload = {
            "record_id": record_id,
            "by": current_user.full_name or current_user.email,
            "comment_id": comment_id,
        }
        await notifications.notify(
            conn,
            current_user.tenant_id,
            mentions,
            "mention",
            payload,
            table_name=safe,
            document_id=doc_id,
            exclude=current_user.id,
        )
        if parent_author and parent_author not in mentions:
            reply_to = await notifications.users_with_permission(
                conn,
                current_user.tenant_id,
                safe,
                doc_id,
                "read",
                user_ids=[parent_author],
            )
            await notifications.notify(
                conn,
                current_user.tenant_id,
                reply_to,
                "comment_reply",
                payload,
                table_name=safe,
                document_id=doc_id,
                exclude=current_user.id,
            )
        row = (
            (
                await conn.execute(
                    text(_SELECT + " WHERE c.id = CAST(:id AS uuid)"),
                    {"id": comment_id},
                )
            )
            .mappings()
            .one()
        )
    await log_action(
        action="comment_added",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "comment_id": comment_id,
            "parent_id": parent_id,
            "version_no": version_no,
            "length": len(text_body),
            "mentions": mentions,
        },
        ip_address=client_ip(request),
    )
    return _out(dict(row), current_user)


class EditCommentBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)
    mentions: list[str] | None = Field(default=None, max_length=MAX_MENTIONS)


@router.patch("/comments/{comment_id}", summary="Edit my comment")
@limiter.limit("60/minute")
async def edit_comment(
    request: Request,
    comment_id: str,
    body: EditCommentBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    text_body = body.body.strip()
    if not text_body:
        raise HTTPException(
            status_code=422, detail=t("comments.empty", current_user.locale)
        )
    async with _engine().begin() as conn:
        comment = await _load(conn, comment_id, current_user)
        if comment["author_id"] != current_user.id or comment["deleted_at"] is not None:
            raise HTTPException(
                status_code=403, detail=t("comments.not_author", current_user.locale)
            )
        safe, doc_id = comment["table_name"], comment["document_id"]
        old = comment["mentions"]
        old = json.loads(old) if isinstance(old, str) else (old or [])
        mentions = (
            await _valid_mentions(conn, current_user, safe, doc_id, body.mentions)
            if body.mentions is not None
            else old
        )
        await conn.execute(
            text("""
                UPDATE sdai_comments SET body = :b, mentions = CAST(:m AS jsonb), edited_at = now()
                WHERE id = CAST(:id AS uuid)
            """),
            {"b": text_body, "m": json.dumps(mentions), "id": comment["id"]},
        )
        added = [m for m in mentions if m not in old]
        if added:
            record_id = (
                await conn.execute(
                    text(
                        f'SELECT record_id FROM "{safe}" WHERE id = CAST(:id AS uuid)'
                    ),
                    {"id": doc_id},
                )
            ).scalar()
            await notifications.notify(
                conn,
                current_user.tenant_id,
                added,
                "mention",
                {
                    "record_id": record_id,
                    "by": current_user.full_name or current_user.email,
                    "comment_id": comment["id"],
                },
                table_name=safe,
                document_id=doc_id,
                exclude=current_user.id,
            )
        row = (
            (
                await conn.execute(
                    text(_SELECT + " WHERE c.id = CAST(:id AS uuid)"),
                    {"id": comment["id"]},
                )
            )
            .mappings()
            .one()
        )
    await log_action(
        action="comment_edited",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "comment_id": comment["id"],
            "length": len(text_body),
            "mentions_added": added,
        },
        ip_address=client_ip(request),
    )
    return _out(dict(row), current_user)


@router.delete("/comments/{comment_id}", summary="Delete a comment")
async def delete_comment(
    request: Request,
    comment_id: str,
    current_user: CurrentUser = Depends(require_reviewer),
):
    async with _engine().begin() as conn:
        comment = await _load(conn, comment_id, current_user)
        if comment["deleted_at"] is not None:
            raise HTTPException(
                status_code=404, detail=t("comments.not_found", current_user.locale)
            )
        if comment["author_id"] != current_user.id and current_user.role != "admin":
            raise HTTPException(
                status_code=403, detail=t("comments.not_author", current_user.locale)
            )
        await conn.execute(
            text("""
                UPDATE sdai_comments SET deleted_at = now(), deleted_by = CAST(:u AS uuid)
                WHERE id = CAST(:id AS uuid)
            """),
            {"u": current_user.id, "id": comment["id"]},
        )
    await log_action(
        action="comment_deleted",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=comment["table_name"],
        document_id=comment["document_id"],
        details={"comment_id": comment["id"], "author_id": comment["author_id"]},
        ip_address=client_ip(request),
    )
    return {"ok": True}
