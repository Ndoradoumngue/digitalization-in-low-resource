"""Classification (GED Phase 3): the plan de classement (series tree).

  GET    /api/db/series                  flat list (parent, code, path, position,
                                         documents visible to the caller per series)
  GET    /api/db/series/tree             the same as a nested tree
  POST   /api/db/series                  create, optionally under a parent (admin)
  PATCH  /api/db/series/{id}             rename / recode / reorder / move (admin)
  DELETE /api/db/series/{id}             delete an empty series (admin)

A series' path is the chain of ids from its root down to itself
('/<root>/.../<self>/'): a subtree is every series whose path starts with
the parent's, and a series-scope grant reaches every document filed
anywhere below the granted series (documents_router._series_grant_match).
Moves rewrite the paths of the whole subtree in one transaction and refuse
cycles. Paths are derived from parent_id - after applying a peer's changes
the sync calls repair_series_paths, which also breaks a cycle two
concurrent moves on two nodes could create, the same way on both nodes.

Assigning a document to a series stays PATCH /api/db/documents/{t}/{id}/series
(documents_router).

Tags (a tenant-wide vocabulary, any number per document):

  GET    /api/db/tags                               vocabulary + documents the caller can see per tag
  POST   /api/db/tags                               create (admin)
  PATCH  /api/db/tags/{id}                          rename / recolour (admin)
  POST   /api/db/tags/{id}/merge                    move every use to another tag, then delete (admin)
  DELETE /api/db/tags/{id}                          delete, untagging every document (admin)
  GET    /api/db/documents/{t}/{id}/tags            a document's tags (read)
  POST   /api/db/documents/{t}/{id}/tags            tag it - existing tag_id, or a name (an unknown
                                                    name creates the tag) (edit)
  DELETE /api/db/documents/{t}/{id}/tags/{tag_id}   untag (edit)

Automatic filing (ingest_router._filing_series, applied when a document is
filed): the series of its type's rule, else the tenant's "À classer" series.

  GET    /api/db/filing-rules                       every document type and its series (admin)
  PUT    /api/db/filing-rules/{table}               {series_id} - null removes the rule (admin)

Link suggestions:

  GET    /api/db/documents/{t}/{id}/link-suggestions   documents this one mentions by their
         reference (reference-like field or record_id) and documents that mention this one's,
         excluding itself, already linked ones and anything the caller can't see. Linking stays
         POST /api/db/documents/{t}/{id}/links.
"""

from __future__ import annotations

import re
import uuid

import lifecycle
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
    _searchable_cols,
    _sanitize,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from i18n import t
from ingest_router import _engine
from pydantic import BaseModel, Field
from sqlalchemy import text

router = APIRouter(prefix="/api/db", tags=["classification"])

_NIL = "00000000-0000-0000-0000-000000000000"
_SERIES_COLS = (
    "id::text AS id, parent_id::text AS parent_id, name, description, code, path, position,"
    " system_key, created_at"
)


def _valid_uuid(value: str, locale: str, key: str = "common.series_not_found") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail=t(key, locale)) from exc


def _serial(row: dict) -> dict:
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()
    }


async def _series_rows(conn, tenant_id: str) -> list[dict]:
    rows = await conn.execute(
        text(
            f"SELECT {_SERIES_COLS} FROM sdai_series WHERE tenant_id = CAST(:tid AS uuid)"
            " ORDER BY position, code NULLS LAST, name"
        ),
        {"tid": tenant_id},
    )
    return [_serial(dict(r)) for r in rows.mappings().all()]


async def _visible_counts(conn, current_user: CurrentUser) -> dict[str, int]:
    """Documents per series the caller can actually see (never counts what
    their access would hide)."""
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    parts = []
    for table, cols in sorted(tables_cols.items()):
        if "series_id" not in cols:
            continue
        clause = _access_where_clause(table, current_user)
        where = (
            f"series_id IS NOT NULL AND {clause}" if clause else "series_id IS NOT NULL"
        )
        parts.append(f'SELECT series_id FROM "{table}" WHERE {where}')
    if not parts:
        return {}
    rows = await conn.execute(
        text(
            f"SELECT series_id::text, count(*) FROM ({' UNION ALL '.join(parts)}) s GROUP BY 1"
        ),
        _access_params(current_user),
    )
    return {r[0]: int(r[1]) for r in rows}


@router.get("/series", summary="List series (plan de classement)")
async def list_series(current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        rows = await _series_rows(conn, current_user.tenant_id)
        counts = await _visible_counts(conn, current_user)
    for row in rows:
        row["document_count"] = counts.get(row["id"], 0)
    return rows


def build_tree(rows: list[dict]) -> list[dict]:
    """Nest a flat, ordered series list. A row whose parent is missing (not
    synced yet) shows at the root rather than disappearing."""
    by_id = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in rows:
        node = by_id[r["id"]]
        parent = by_id.get(r["parent_id"]) if r["parent_id"] else None
        (parent["children"] if parent else roots).append(node)
    return roots


@router.get("/series/tree", summary="Series as a tree")
async def series_tree(current_user: CurrentUser = Depends(require_user)):
    return build_tree(await list_series(current_user))


class CreateSeriesBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    parent_id: str | None = None
    code: str | None = Field(None, max_length=50)
    position: int = 0


class UpdateSeriesBody(BaseModel):
    """Only the fields sent are changed; parent_id null moves to the root."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    parent_id: str | None = None
    code: str | None = Field(None, max_length=50)
    position: int | None = None


async def _load_series(
    conn, series_id: str, tenant_id: str, locale: str, lock: bool = False
) -> dict:
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT {_SERIES_COLS} FROM sdai_series"
                    f" WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid){' FOR UPDATE' if lock else ''}"
                ),
                {"id": _valid_uuid(series_id, locale), "tid": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=t("common.series_not_found", locale)
        )
    return dict(row)


def _duplicate_name(locale: str, name: str) -> HTTPException:
    return HTTPException(
        status_code=409, detail=t("common.series_already_exists", locale, name=name)
    )


@router.post("/series", summary="Create a series")
async def create_series(
    body: CreateSeriesBody, current_user: CurrentUser = Depends(require_admin)
):
    locale = current_user.locale
    async with _engine().begin() as conn:
        parent_path = "/"
        if body.parent_id:
            parent = await _load_series(
                conn, body.parent_id, current_user.tenant_id, locale
            )
            parent_path = parent["path"]
        new_id = str(uuid.uuid4())
        result = await conn.execute(
            text(f"""
                INSERT INTO sdai_series
                    (id, tenant_id, parent_id, name, description, code, position, path, created_by)
                VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:parent AS uuid), :name, :description,
                        :code, :position, :path, CAST(:cb AS uuid))
                ON CONFLICT (tenant_id, (COALESCE(parent_id, '{_NIL}'::uuid)), name) DO NOTHING
                RETURNING id
            """),
            {
                "id": new_id,
                "tid": current_user.tenant_id,
                "parent": body.parent_id,
                "name": body.name,
                "description": body.description,
                "code": body.code,
                "position": body.position,
                "path": f"{parent_path}{new_id}/",
                "cb": current_user.id,
            },
        )
        if result.scalar() is None:
            raise _duplicate_name(locale, body.name)

    await log_action(
        action="series_created",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"series_id": new_id, "name": body.name, "parent_id": body.parent_id},
    )
    return {
        "id": new_id,
        "name": body.name,
        "parent_id": body.parent_id,
        "path": f"{parent_path}{new_id}/",
    }


@router.patch("/series/{series_id}", summary="Rename, recode, reorder or move a series")
async def update_series(
    series_id: str,
    body: UpdateSeriesBody,
    current_user: CurrentUser = Depends(require_admin),
):
    locale = current_user.locale
    sent = body.model_fields_set
    async with _engine().begin() as conn:
        # One move at a time per tenant: two concurrent moves could each
        # pass the cycle check against the other's old paths.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('sdai_series_move:' || :tid))"),
            {"tid": current_user.tenant_id},
        )
        series = await _load_series(
            conn, series_id, current_user.tenant_id, locale, lock=True
        )
        changes: dict = {}
        for field in ("name", "description", "code", "position"):
            if field in sent and getattr(body, field) != series[field]:
                if field in ("name", "position") and getattr(body, field) is None:
                    continue
                changes[field] = getattr(body, field)

        moved = "parent_id" in sent and (body.parent_id or None) != series["parent_id"]
        new_path = series["path"]
        if moved:
            parent_path = "/"
            if body.parent_id:
                parent = await _load_series(
                    conn, body.parent_id, current_user.tenant_id, locale
                )
                if parent["path"].startswith(series["path"]):
                    raise HTTPException(
                        status_code=409, detail=t("classification.series_cycle", locale)
                    )
                parent_path = parent["path"]
            new_path = f"{parent_path}{series['id']}/"
            changes["parent_id"] = body.parent_id or None

        if changes:
            assignments = ", ".join(
                "parent_id = CAST(:parent_id AS uuid)"
                if k == "parent_id"
                else f"{k} = :{k}"
                for k in changes
            )
            exists = (
                await conn.execute(
                    text(f"""
                        SELECT 1 FROM sdai_series
                        WHERE tenant_id = CAST(:tid AS uuid) AND id <> CAST(:id AS uuid) AND name = :name
                          AND COALESCE(parent_id, '{_NIL}'::uuid)
                              = COALESCE(CAST(:parent AS uuid), '{_NIL}'::uuid)
                    """),
                    {
                        "tid": current_user.tenant_id,
                        "id": series["id"],
                        "name": changes.get("name", series["name"]),
                        "parent": changes.get("parent_id", series["parent_id"]),
                    },
                )
            ).one_or_none()
            if exists:
                raise _duplicate_name(locale, changes.get("name", series["name"]))
            await conn.execute(
                text(
                    f"UPDATE sdai_series SET {assignments} WHERE id = CAST(:id AS uuid)"
                ),
                {**changes, "id": series["id"]},
            )
        if moved:
            # The series and its whole subtree, in this same transaction.
            await conn.execute(
                text("""
                    UPDATE sdai_series
                    SET path = :new || substr(path, length(:old) + 1)
                    WHERE tenant_id = CAST(:tid AS uuid) AND starts_with(path, :old)
                """),
                {"new": new_path, "old": series["path"], "tid": current_user.tenant_id},
            )
            # Retention rules and legal holds follow the tree.
            await retention.recompute(conn, current_user.tenant_id)
            tables = await _get_tables_columns(conn, current_user.tenant_slug)
            await lifecycle.refresh_hold_flags(conn, sorted(tables))

    if changes:
        await log_action(
            action="series_moved" if moved else "series_updated",
            user_id=current_user.id,
            user_email=current_user.email,
            details={
                "series_id": series["id"],
                "changes": changes,
                "from_parent": series["parent_id"],
            },
        )
    if moved:
        # Grants on ancestors now reach (or stop reaching) this subtree.
        await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"id": series["id"], "path": new_path, **{k: v for k, v in changes.items()}}


@router.delete("/series/{series_id}", summary="Delete an empty series")
async def delete_series(
    series_id: str, current_user: CurrentUser = Depends(require_admin)
):
    """Only an empty series can go: no sub-series, no document (trashed ones
    included - they may come back), no automatic-filing rule pointing at it.
    Grants on it go with it."""
    locale = current_user.locale
    async with _engine().begin() as conn:
        series = await _load_series(
            conn, series_id, current_user.tenant_id, locale, lock=True
        )
        children = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM sdai_series WHERE parent_id = CAST(:id AS uuid)"
                ),
                {"id": series["id"]},
            )
        ).scalar()
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        documents = 0
        for table, cols in tables_cols.items():
            if "series_id" in cols:
                documents += (
                    await conn.execute(
                        text(
                            f'SELECT count(*) FROM "{table}" WHERE series_id = CAST(:id AS uuid)'
                        ),
                        {"id": series["id"]},
                    )
                ).scalar()
        rules = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM sdai_type_series_defaults WHERE series_id = CAST(:id AS uuid)"
                ),
                {"id": series["id"]},
            )
        ).scalar()
        if children or documents or rules:
            raise HTTPException(
                status_code=409,
                detail=t(
                    "classification.series_not_empty",
                    locale,
                    children=children,
                    documents=documents,
                    rules=rules,
                ),
            )
        await conn.execute(
            text("DELETE FROM sdai_series WHERE id = CAST(:id AS uuid)"),
            {"id": series["id"]},
        )

    await log_action(
        action="series_deleted",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"series_id": series["id"], "name": series["name"]},
    )
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"ok": True}


# ── Sync support ──────────────────────────────────────────────────────────────


def compute_paths(
    rows: list[tuple[str, str | None]],
) -> tuple[dict[str, str], list[str]]:
    """Paths for (id, parent_id) rows, and the ids whose parent link must be
    cut to break cycles - the smallest id of each cycle, so every node that
    runs this on the same rows takes the same decision. A parent that does
    not exist makes a root."""
    parent = {sid: pid for sid, pid in rows}
    cut: list[str] = []
    state: dict[str, int] = {}  # 1 = on the current walk, 2 = done
    for start in sorted(parent):
        walk = []
        node = start
        while node is not None and node in parent and state.get(node) != 2:
            if state.get(node) == 1:
                cycle = walk[walk.index(node) :]
                breaker = min(cycle)
                cut.append(breaker)
                parent[breaker] = None
                break
            state[node] = 1
            walk.append(node)
            node = parent[node]
        for n in walk:
            state[n] = 2
    paths: dict[str, str] = {}

    def path_of(sid: str) -> str:
        if sid in paths:
            return paths[sid]
        chain = []
        node = sid
        while node is not None and node in parent and node not in paths:
            chain.append(node)
            node = parent[node]
        prefix = paths[node] if node in paths else "/"
        for n in reversed(chain):
            prefix = f"{prefix}{n}/"
            paths[n] = prefix
        return paths[sid]

    for sid in sorted(parent):
        path_of(sid)
    return paths, sorted(cut)


async def repair_series_paths(conn) -> int:
    """Recompute every series path from parent_id and break cycles (two
    nodes moving series under each other while disconnected). Run by the
    sync after applying a peer's changes to sdai_series, in the same
    transaction - deterministic, so both nodes end up identical. Returns
    the number of rows fixed."""
    rows = (
        await conn.execute(text("SELECT id::text, parent_id::text FROM sdai_series"))
    ).all()
    paths, cut = compute_paths([(r[0], r[1]) for r in rows])
    for sid in cut:
        await conn.execute(
            text(
                "UPDATE sdai_series SET parent_id = NULL WHERE id = CAST(:id AS uuid)"
            ),
            {"id": sid},
        )
    current = {
        r[0]: r[1]
        for r in await conn.execute(text("SELECT id::text, path FROM sdai_series"))
    }
    fixed = 0
    for sid, path in paths.items():
        if current.get(sid) != path:
            await conn.execute(
                text("UPDATE sdai_series SET path = :p WHERE id = CAST(:id AS uuid)"),
                {"p": path, "id": sid},
            )
            fixed += 1
    return fixed + len(cut)


# ── Tags ──────────────────────────────────────────────────────────────────────

_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class CreateTagBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str | None = Field(None, pattern=_COLOR_PATTERN)


class UpdateTagBody(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    color: str | None = Field(None, pattern=_COLOR_PATTERN)


class MergeTagBody(BaseModel):
    into_id: str


class TagDocumentBody(BaseModel):
    tag_id: str | None = None
    name: str | None = Field(None, min_length=1, max_length=60)


def _tag_json(row) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "color": row["color"]}


async def _tag_counts(conn, current_user: CurrentUser) -> dict[str, int]:
    """Documents per tag the caller can see (counts never reveal hidden ones)."""
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    parts = []
    for table in sorted(tables_cols):
        clause = _access_where_clause(table, current_user)
        parts.append(
            f"SELECT dt.tag_id FROM sdai_document_tags dt"
            f' JOIN "{table}" ON "{table}".id = dt.document_id'
            f" WHERE dt.tenant_id = CAST(:__access_tenant_id AS uuid) AND dt.table_name = '{table}'"
            + (f" AND {clause}" if clause else "")
        )
    if not parts:
        return {}
    rows = await conn.execute(
        text(
            f"SELECT tag_id::text, count(*) FROM ({' UNION ALL '.join(parts)}) x GROUP BY 1"
        ),
        _access_params(current_user),
    )
    return {r[0]: int(r[1]) for r in rows}


@router.get("/tags", summary="Tag vocabulary of this tenant")
async def list_tags(current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT id, name, color FROM sdai_tags WHERE tenant_id = CAST(:tid AS uuid) ORDER BY lower(name)"
            ),
            {"tid": current_user.tenant_id},
        )
        tags = [_tag_json(r) for r in rows.mappings()]
        counts = await _tag_counts(conn, current_user)
    for tag in tags:
        tag["document_count"] = counts.get(tag["id"], 0)
    return tags


async def _get_or_create_tag(
    conn, current_user: CurrentUser, name: str, color: str | None = None
) -> tuple[dict, bool]:
    name = name.strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail=t("classification.tag_name_required", current_user.locale),
        )
    existing = (
        (
            await conn.execute(
                text(
                    "SELECT id, name, color FROM sdai_tags"
                    " WHERE tenant_id = CAST(:tid AS uuid) AND lower(name) = lower(:n)"
                ),
                {"tid": current_user.tenant_id, "n": name},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        return _tag_json(existing), False
    row = (
        (
            await conn.execute(
                text("""
                INSERT INTO sdai_tags (tenant_id, name, color, created_by)
                VALUES (CAST(:tid AS uuid), :n, :c, CAST(:u AS uuid))
                ON CONFLICT (tenant_id, lower(name)) DO UPDATE SET name = sdai_tags.name
                RETURNING id, name, color
            """),
                {
                    "tid": current_user.tenant_id,
                    "n": name,
                    "c": color,
                    "u": current_user.id,
                },
            )
        )
        .mappings()
        .one()
    )
    return _tag_json(row), True


async def _load_tag(conn, tag_id: str, current_user: CurrentUser) -> dict:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT id, name, color FROM sdai_tags WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {
                    "id": _valid_uuid(
                        tag_id, current_user.locale, "classification.tag_not_found"
                    ),
                    "tid": current_user.tenant_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=t("classification.tag_not_found", current_user.locale),
        )
    return _tag_json(row)


@router.post("/tags", summary="Create a tag")
async def create_tag(
    body: CreateTagBody, current_user: CurrentUser = Depends(require_admin)
):
    async with _engine().begin() as conn:
        tag, created = await _get_or_create_tag(
            conn, current_user, body.name, body.color
        )
    if not created:
        raise HTTPException(
            status_code=409,
            detail=t(
                "classification.tag_exists", current_user.locale, name=tag["name"]
            ),
        )
    await log_action(
        action="tag_created",
        user_id=current_user.id,
        user_email=current_user.email,
        details=tag,
    )
    return tag


@router.patch("/tags/{tag_id}", summary="Rename or recolour a tag")
async def update_tag(
    tag_id: str, body: UpdateTagBody, current_user: CurrentUser = Depends(require_admin)
):
    sent = body.model_fields_set
    async with _engine().begin() as conn:
        tag = await _load_tag(conn, tag_id, current_user)
        changes = {
            k: getattr(body, k)
            for k in ("name", "color")
            if k in sent and getattr(body, k) != tag[k]
        }
        if "name" in changes and changes["name"] is None:
            changes.pop("name")
        if "name" in changes:
            clash = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM sdai_tags WHERE tenant_id = CAST(:tid AS uuid)"
                        " AND lower(name) = lower(:n) AND id <> CAST(:id AS uuid)"
                    ),
                    {
                        "tid": current_user.tenant_id,
                        "n": changes["name"],
                        "id": tag["id"],
                    },
                )
            ).one_or_none()
            if clash:
                raise HTTPException(
                    status_code=409,
                    detail=t(
                        "classification.tag_exists",
                        current_user.locale,
                        name=changes["name"],
                    ),
                )
        if changes:
            await conn.execute(
                text(
                    f"UPDATE sdai_tags SET {', '.join(f'{k} = :{k}' for k in changes)} WHERE id = CAST(:id AS uuid)"
                ),
                {**changes, "id": tag["id"]},
            )
    if changes:
        await log_action(
            action="tag_updated",
            user_id=current_user.id,
            user_email=current_user.email,
            details={"tag_id": tag["id"], "from": tag, "changes": changes},
        )
    return {**tag, **changes}


@router.post("/tags/{tag_id}/merge", summary="Merge a tag into another")
async def merge_tag(
    tag_id: str, body: MergeTagBody, current_user: CurrentUser = Depends(require_admin)
):
    async with _engine().begin() as conn:
        source = await _load_tag(conn, tag_id, current_user)
        target = await _load_tag(conn, body.into_id, current_user)
        if source["id"] == target["id"]:
            raise HTTPException(
                status_code=400,
                detail=t("classification.tag_merge_self", current_user.locale),
            )
        moved = await conn.execute(
            text("""
                INSERT INTO sdai_document_tags (tenant_id, table_name, document_id, tag_id, created_by)
                SELECT tenant_id, table_name, document_id, CAST(:into AS uuid), CAST(:u AS uuid)
                FROM sdai_document_tags WHERE tag_id = CAST(:src AS uuid)
                ON CONFLICT (tenant_id, table_name, document_id, tag_id) DO NOTHING
            """),
            {"into": target["id"], "src": source["id"], "u": current_user.id},
        )
        await conn.execute(
            text("DELETE FROM sdai_tags WHERE id = CAST(:id AS uuid)"),
            {"id": source["id"]},
        )
    await log_action(
        action="tags_merged",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"from": source, "into": target, "documents_moved": moved.rowcount},
    )
    return {"into": target, "documents_moved": moved.rowcount}


@router.delete("/tags/{tag_id}", summary="Delete a tag")
async def delete_tag(tag_id: str, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        tag = await _load_tag(conn, tag_id, current_user)
        # Untagged one by one (not by cascade) so each removal is a captured,
        # synchronised change like any other.
        await conn.execute(
            text("DELETE FROM sdai_document_tags WHERE tag_id = CAST(:id AS uuid)"),
            {"id": tag["id"]},
        )
        await conn.execute(
            text("DELETE FROM sdai_tags WHERE id = CAST(:id AS uuid)"),
            {"id": tag["id"]},
        )
    await log_action(
        action="tag_deleted",
        user_id=current_user.id,
        user_email=current_user.email,
        details=tag,
    )
    return {"ok": True}


async def _document_scope(
    conn, table_name: str, doc_id: str, current_user: CurrentUser, permission: str
) -> str:
    safe = _sanitize(table_name)
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    if safe not in tables_cols:
        raise HTTPException(
            status_code=404,
            detail=t("common.table_not_found", current_user.locale, table=safe),
        )
    _valid_uuid(doc_id, current_user.locale, "common.document_not_found")
    await _require_document_permission(conn, safe, doc_id, current_user, permission)
    return safe


async def _document_tags(conn, tenant_id: str, table: str, doc_id: str) -> list[dict]:
    rows = await conn.execute(
        text("""
            SELECT t.id, t.name, t.color FROM sdai_document_tags dt
            JOIN sdai_tags t ON t.id = dt.tag_id
            WHERE dt.tenant_id = CAST(:tid AS uuid) AND dt.table_name = :tb AND dt.document_id = CAST(:d AS uuid)
            ORDER BY lower(t.name)
        """),
        {"tid": tenant_id, "tb": table, "d": doc_id},
    )
    return [_tag_json(r) for r in rows.mappings()]


@router.get("/documents/{table_name}/{doc_id}/tags", summary="A document's tags")
async def get_document_tags(
    table_name: str, doc_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().connect() as conn:
        safe = await _document_scope(conn, table_name, doc_id, current_user, "read")
        return await _document_tags(conn, current_user.tenant_id, safe, doc_id)


@router.post("/documents/{table_name}/{doc_id}/tags", summary="Tag a document")
async def tag_document(
    table_name: str,
    doc_id: str,
    body: TagDocumentBody,
    request: Request,
    current_user: CurrentUser = Depends(require_reviewer),
):
    if bool(body.tag_id) == bool(body.name):
        raise HTTPException(
            status_code=422,
            detail=t("classification.tag_name_required", current_user.locale),
        )
    async with _engine().begin() as conn:
        safe = await _document_scope(conn, table_name, doc_id, current_user, "edit")
        if body.tag_id:
            tag, created = await _load_tag(conn, body.tag_id, current_user), False
        else:
            tag, created = await _get_or_create_tag(conn, current_user, body.name)
        await conn.execute(
            text("""
                INSERT INTO sdai_document_tags (tenant_id, table_name, document_id, tag_id, created_by)
                VALUES (CAST(:tid AS uuid), :tb, CAST(:d AS uuid), CAST(:tag AS uuid), CAST(:u AS uuid))
                ON CONFLICT (tenant_id, table_name, document_id, tag_id) DO NOTHING
            """),
            {
                "tid": current_user.tenant_id,
                "tb": safe,
                "d": doc_id,
                "tag": tag["id"],
                "u": current_user.id,
            },
        )
        tags = await _document_tags(conn, current_user.tenant_id, safe, doc_id)
    await log_action(
        action="document_tagged",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"tag_id": tag["id"], "name": tag["name"], "tag_created": created},
        ip_address=client_ip(request),
    )
    return tags


@router.delete(
    "/documents/{table_name}/{doc_id}/tags/{tag_id}", summary="Untag a document"
)
async def untag_document(
    table_name: str,
    doc_id: str,
    tag_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_reviewer),
):
    async with _engine().begin() as conn:
        safe = await _document_scope(conn, table_name, doc_id, current_user, "edit")
        tag = await _load_tag(conn, tag_id, current_user)
        await conn.execute(
            text("""
                DELETE FROM sdai_document_tags
                WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :tb
                  AND document_id = CAST(:d AS uuid) AND tag_id = CAST(:tag AS uuid)
            """),
            {"tid": current_user.tenant_id, "tb": safe, "d": doc_id, "tag": tag["id"]},
        )
        tags = await _document_tags(conn, current_user.tenant_id, safe, doc_id)
    await log_action(
        action="document_untagged",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"tag_id": tag["id"], "name": tag["name"]},
        ip_address=client_ip(request),
    )
    return tags


# ── Automatic filing rules ────────────────────────────────────────────────────


class FilingRuleBody(BaseModel):
    series_id: str | None = None


@router.get("/filing-rules", summary="Series each document type is filed into")
async def list_filing_rules(current_user: CurrentUser = Depends(require_admin)):
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        rules = {
            r[0]: r[1]
            for r in await conn.execute(
                text(
                    "SELECT table_name, series_id::text FROM sdai_type_series_defaults"
                    " WHERE tenant_id = CAST(:tid AS uuid)"
                ),
                {"tid": current_user.tenant_id},
            )
        }
        fallback = (
            await conn.execute(
                text(
                    "SELECT id::text FROM sdai_series"
                    " WHERE tenant_id = CAST(:tid AS uuid) AND system_key = 'to_be_filed'"
                ),
                {"tid": current_user.tenant_id},
            )
        ).scalar()
    return {
        "rules": [
            {"table_name": tbl, "series_id": rules.get(tbl)}
            for tbl in sorted(tables_cols)
        ],
        "to_be_filed_series_id": fallback,
    }


@router.put("/filing-rules/{table_name}", summary="Set or remove a type's filing rule")
async def set_filing_rule(
    table_name: str,
    body: FilingRuleBody,
    current_user: CurrentUser = Depends(require_admin),
):
    safe = _sanitize(table_name)
    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404,
                detail=t("common.table_not_found", current_user.locale, table=safe),
            )
        if body.series_id is None:
            await conn.execute(
                text(
                    "DELETE FROM sdai_type_series_defaults"
                    " WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t"
                ),
                {"tid": current_user.tenant_id, "t": safe},
            )
        else:
            series = await _load_series(
                conn, body.series_id, current_user.tenant_id, current_user.locale
            )
            await conn.execute(
                text("""
                    INSERT INTO sdai_type_series_defaults (tenant_id, table_name, series_id, created_by)
                    VALUES (CAST(:tid AS uuid), :t, CAST(:s AS uuid), CAST(:u AS uuid))
                    ON CONFLICT (tenant_id, table_name) DO UPDATE SET series_id = EXCLUDED.series_id
                """),
                {
                    "tid": current_user.tenant_id,
                    "t": safe,
                    "s": series["id"],
                    "u": current_user.id,
                },
            )
    await log_action(
        action="filing_rule_set",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        details={"series_id": body.series_id},
    )
    return {"table_name": safe, "series_id": body.series_id}


# ── Link suggestions ──────────────────────────────────────────────────────────

# Field names that hold a document's own reference (reference_number,
# num_titre, ref, numero_dossier, ...), plus the citable record_id.
_REFERENCE_NAME = re.compile(
    r"(^|_)(ref|refs|reference|references|numero|num|number|no|n)(_|$)"
)
SUGGESTION_LIMIT = 20


def _reference_cols(cols: dict[str, str]) -> list[str]:
    searchable = _searchable_cols(cols)
    found = [c for c in searchable if _REFERENCE_NAME.search(c)]
    if "record_id" in cols and "record_id" not in found:
        found.append("record_id")
    return found


def _is_reference(value) -> bool:
    """Worth matching: long enough and carries a digit (a bare word such as
    'arrêté' would match half the archive)."""
    return (
        isinstance(value, str)
        and len(value.strip()) >= 4
        and any(ch.isdigit() for ch in value)
    )


@router.get(
    "/documents/{table_name}/{doc_id}/link-suggestions",
    summary="Documents this one may be linked to",
)
async def link_suggestions(
    table_name: str, doc_id: str, current_user: CurrentUser = Depends(require_user)
):
    async with _engine().connect() as conn:
        safe = await _document_scope(conn, table_name, doc_id, current_user, "read")
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        cols = tables_cols[safe]
        row = (
            (
                await conn.execute(
                    text(f'SELECT * FROM "{safe}" WHERE id = CAST(:id AS uuid)'),
                    {"id": doc_id},
                )
            )
            .mappings()
            .one()
        )
        own_refs = {
            v.strip() for c in _reference_cols(cols) if _is_reference(v := row.get(c))
        }
        own_text = " ".join(
            str(row[c]) for c in _searchable_cols(cols) if row.get(c)
        ).lower()
        linked = {
            (r[0], str(r[1]))
            for r in await conn.execute(
                text("""
                    SELECT to_table, to_id FROM sdai_document_links
                    WHERE tenant_id = CAST(:tid AS uuid) AND from_table = :t AND from_id = CAST(:id AS uuid)
                    UNION
                    SELECT from_table, from_id FROM sdai_document_links
                    WHERE tenant_id = CAST(:tid AS uuid) AND to_table = :t AND to_id = CAST(:id AS uuid)
                """),
                {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
            )
        }

        found: dict[tuple[str, str], dict] = {}
        params = {"__self": doc_id, "__text": own_text, **_access_params(current_user)}
        for table, tcols in sorted(tables_cols.items()):
            access = _access_where_clause(table, current_user)
            base = f'"{table}".id <> CAST(:__self AS uuid)' if table == safe else "TRUE"
            if access:
                base += f" AND {access}"
            display = _display_col(tcols)
            pick = (
                f"id::text AS id, {'record_id' if 'record_id' in tcols else 'NULL'}::text AS record_id,"
                f" {'document_type' if 'document_type' in tcols else 'NULL'}::text AS document_type,"
                f" {display if display else 'NULL'}::text AS label"
            )
            # This document mentions theirs: one of their reference values
            # appears in our text.
            for ref_col in _reference_cols(tcols):
                rows = await conn.execute(
                    text(f"""
                        SELECT {pick}, {ref_col}::text AS matched FROM "{table}"
                        WHERE {base} AND length({ref_col}) >= 4 AND {ref_col} ~ '[0-9]'
                          AND strpos(:__text, lower({ref_col})) > 0
                        LIMIT {SUGGESTION_LIMIT}
                    """),
                    params,
                )
                for r in rows.mappings():
                    found.setdefault(
                        (table, r["id"]),
                        {**r, "field": ref_col, "direction": "mentions"},
                    )
            # Theirs mention ours: our reference values appear in their text
            # - the French full-text index narrows it down, an exact
            # substring check confirms.
            searchable = _searchable_cols(tcols)
            if not searchable or not own_refs:
                continue
            doc_text = " || ' ' || ".join(f"COALESCE({c},'')" for c in searchable)
            for i, ref in enumerate(sorted(own_refs)):
                rows = await conn.execute(
                    text(f"""
                        SELECT {pick}, CAST(:__ref{i} AS text) AS matched FROM "{table}"
                        WHERE {base}
                          AND to_tsvector('french', {doc_text}) @@ plainto_tsquery('french', :__ref{i})
                          AND strpos(lower({doc_text}), lower(:__ref{i})) > 0
                        LIMIT {SUGGESTION_LIMIT}
                    """),
                    {**params, f"__ref{i}": ref},
                )
                for r in rows.mappings():
                    found.setdefault(
                        (table, r["id"]),
                        {**r, "field": None, "direction": "mentioned_by"},
                    )

    suggestions = [
        {"table_name": table, **item}
        for (table, item_id), item in found.items()
        if (table, item_id) not in linked
    ]
    suggestions.sort(
        key=lambda s: (s["direction"] != "mentions", s["table_name"], s["label"] or "")
    )
    return suggestions[:SUGGESTION_LIMIT]
