"""
Document browser — queries the dynamically-inferred PostgreSQL tables created
by the ingestion pipeline.  Table names are always discovered via
information_schema; they are never hardcoded or taken raw from user input.

Endpoints:
  GET    /api/db/documents                    — paginated list with filters & full-text search
  GET    /api/db/documents/{tbl}/{id}         — full field detail for one document
  PATCH  /api/db/documents/{tbl}/{id}/fields  — edit extraction field data (permission-gated)
  GET    /api/db/documents/{tbl}/{id}/links   — chain-of-custody links for one document
  POST   /api/db/documents/{tbl}/{id}/links   — link this document to another
  DELETE /api/db/links/{link_id}              — remove a link
  GET    /api/db/documents/{tbl}/{id}/access  — access grants on one document
  POST   /api/db/documents/{tbl}/{id}/access  — grant a group/user access (restricts it)
  DELETE /api/db/access/{grant_id}            — remove an access grant
  GET    /api/db/grantees                     — groups/users available to tag a document with
  GET    /api/db/series                       — list this tenant's series
  POST   /api/db/series                       — create a series (admin-only)
  DELETE /api/db/series/{id}                  — delete a series (admin-only)
  PATCH  /api/db/documents/{tbl}/{id}/series   — assign/unassign a document's series
  GET    /api/db/types                        — table inventory with counts
  GET    /api/db/reviewers                    — users who have reviewed at least one document
  GET    /api/db/image                        — serve a processed image, original PDF,
                                                 or path/Drive-ingested source file
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from audit import client_ip, log_action
from auth import CurrentUser, get_current_user, require_access_manager, require_admin, require_extraction_editor
from cache import _path_key_builder, invalidate_cache
from fastapi_cache.decorator import cache
from i18n import t
from ingest_router import DOCS_DIR as _INGEST_DOCS_DIR
from ingest_router import UPLOADS_DIR as _INGEST_UPLOADS_DIR
from ingest_router import _engine  # lazy accessor — None until startup()
from rate_limit import limiter

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

# Directories the /image endpoint is allowed to serve files from —
# processed page images, original uploads (incl. source PDFs), and files
# ingested via a server path or Google Drive.
_SAFE_FILE_ROOTS = [
    (DATA_DIR / "images").resolve(),
    _INGEST_UPLOADS_DIR.resolve(),
    _INGEST_DOCS_DIR.resolve(),
]

# ── Identifier helpers ────────────────────────────────────────────────────────

def _sanitize(name: str) -> str:
    """Sanitize a user-supplied string to a safe PostgreSQL identifier."""
    name = re.sub(r"[\s\-]+", "_", name.lower().strip())
    name = re.sub(r"[^a-z0-9_]", "", name)
    name = re.sub(r"^[0-9_]+", "", name)
    return (name or "document")[:63]


# ── Table/column discovery ────────────────────────────────────────────────────

async def _get_tables_columns(conn, tenant_slug: str) -> dict[str, dict[str, str]]:
    """
    Return {table_name: {col_name: data_type, ...}} for every ingest-created
    table belonging to this tenant. Ingest tables are identified by having a
    'source_image_path' column and a name that starts with the tenant's
    "t_<slug>_" prefix (see ingest_router._tenant_table_name).

    Uses starts_with(), not LIKE ... || '%' — LIKE's '_' wildcard would
    let e.g. tenant 'land' match a different tenant's table like
    't_landowner_...'. This one function is what tenant-scopes nearly
    every endpoint in this module and in review_router.py, since both
    route all table access through it before touching any table by name.

    Carries each column's Postgres data_type (not just its name) so callers
    can classify columns (e.g. "is this TEXT and therefore searchable?")
    without a second information_schema round trip — see _searchable_cols/
    _extra_cols below.
    """
    result = await conn.execute(
        text("""
            SELECT c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            WHERE c.table_schema = 'public'
              AND starts_with(c.table_name, :prefix)
              AND EXISTS (
                  SELECT 1 FROM information_schema.columns c2
                  WHERE c2.table_schema = 'public'
                    AND c2.table_name = c.table_name
                    AND c2.column_name = 'source_image_path'
              )
            ORDER BY c.table_name, c.column_name
        """),
        {"prefix": f"t_{tenant_slug}_"},
    )
    tables: dict[str, dict[str, str]] = {}
    for table_name, column_name, data_type in result:
        tables.setdefault(table_name, {})[column_name] = data_type
    return tables


# ── UNION query builder ───────────────────────────────────────────────────────

# System/bookkeeping columns every ingest table has — never part of a
# document's extracted, tenant-schema-specific field set. Kept in sync with
# ingest_router._ensure_table's base_cols/reviewed_at/reviewed_by columns.
_BASE_COLS = frozenset({
    "id", "source_image_path", "source_pdf_path", "page_image_paths",
    "batch_id", "batch_document_id", "ingested_at", "confidence",
    "review_status", "content_hash", "reviewed_at", "reviewed_by",
    "series_id", "uploaded_by",
})

# Columns an extraction-editor is never allowed to overwrite directly —
# shared between review_router.patch_review's approve action and
# update_document_fields below, so the two field-editing entry points
# can't drift apart on what's off-limits.
_PROTECTED_FIELDS = frozenset({
    "id", "ingested_at", "review_status", "source_image_path",
    "table_name", "batch_id",
})


def _build_field_set_clause(
    cols: dict[str, str], fields: dict, protected: frozenset[str] = _PROTECTED_FIELDS,
) -> tuple[dict, list[str], dict]:
    """Column-type-aware SET-clause builder for editing extraction field
    values. Returns (editable, set_parts, params): `editable` is the
    submitted fields filtered down to real, non-protected columns (empty
    means nothing there was actually an edit — useful for permission
    checks that only apply when real changes are being made); `set_parts`
    /`params` are ready to merge into an UPDATE's SET clause and bind
    params (params keyed v_<col>, no collision with a caller's own keys)."""
    editable = {k: v for k, v in fields.items() if k in cols and k not in protected}
    set_parts: list[str] = []
    params: dict = {}
    for col, val in editable.items():
        pname    = f"v_{col}"
        col_type = cols.get(col, "text")
        is_jsonb = col_type in ("json", "jsonb")
        if val is None or val == "":
            set_parts.append(f'"{col}" = :{pname}')
            params[pname] = None
        elif is_jsonb and isinstance(val, list):
            set_parts.append(f'"{col}" = CAST(:{pname} AS jsonb)')
            params[pname] = json.dumps(val)
        elif is_jsonb and isinstance(val, str):
            # Comma-separated string → JSON array
            arr = [s.strip() for s in val.split(",") if s.strip()]
            set_parts.append(f'"{col}" = CAST(:{pname} AS jsonb)')
            params[pname] = json.dumps(arr)
        else:
            set_parts.append(f'"{col}" = :{pname}')
            params[pname] = str(val)
    return editable, set_parts, params


def _searchable_cols(cols: dict[str, str]) -> list[str]:
    """TEXT-typed, non-system columns — eligible for full-text search.
    document_type is intentionally included (unlike the old hardcoded
    field list) — it's just another VLM-extracted TEXT column, and
    matching on it is a harmless, minor search improvement."""
    return [c for c, dtype in cols.items() if dtype == "text" and c not in _BASE_COLS]


def _extra_cols(cols: dict[str, str]) -> list[str]:
    """All non-system columns except document_type and record_id (which
    get their own dedicated projected columns) — the key set for the
    extra_fields blob."""
    return [c for c in cols if c not in _BASE_COLS and c not in ("document_type", "record_id")]


def _display_col(cols: dict[str, str]) -> Optional[str]:
    """Best single column to show as a document's "title" in a link/search
    summary — prefers reference_number for familiarity, else the first
    searchable column alphabetically. None if the table has no TEXT field
    at all (e.g. an all-JSONB schema)."""
    searchable = _searchable_cols(cols)
    if "reference_number" in searchable:
        return "reference_number"
    return sorted(searchable)[0] if searchable else None


# ── Document-level access control ─────────────────────────────────────────────
# A document with no rows in sdai_document_access is visible to everyone in
# its tenant (the default/common case, unchanged from before this feature).
# A document with any rows is restricted to admins plus whoever/whatever
# group is explicitly granted — access is always explicit tagging, never
# automatic/blanket group visibility (see CONTEXT.md "Access Control").

def _access_where_clause(table: str, current_user: CurrentUser) -> str:
    """Boolean SQL expression, safe to AND into any WHERE clause (or use
    standalone) selecting from `table`, that's true iff current_user may
    see a given row. Admins get "" (no restriction — see everything);
    callers should skip appending it when empty rather than adding a
    redundant "AND ''"/"AND TRUE". `table` is embedded as a literal —
    always an internally-generated sanitized identifier here, never raw
    user input, matching how table names are already embedded elsewhere
    in this module (e.g. `FROM "{table}"`). Callers MUST also merge
    _access_params(current_user) into the query's params dict whenever
    this returns non-empty."""
    if current_user.role == "admin":
        return ""
    # The correlated `id` reference below MUST be qualified — sdai_document_access
    # has its own `id` primary-key column, so a bare `id` resolves to that
    # instead of correlating to the outer document row, silently turning both
    # branches false (NOT EXISTS always true, i.e. no restriction ever takes
    # effect). `"{table}".id` uses the double-quoted table name as the FROM
    # clause's implicit alias, which every caller relies on (none of them
    # alias the outer table explicitly).
    doc_id_ref = f'"{table}".id'
    return f"""(
        NOT EXISTS (
            SELECT 1 FROM sdai_document_access da
            WHERE da.table_name = '{table}' AND da.document_id = {doc_id_ref}
        )
        OR EXISTS (
            SELECT 1 FROM sdai_document_access da
            WHERE da.table_name = '{table}' AND da.document_id = {doc_id_ref}
              AND ((da.grantee_type = 'user' AND da.grantee_id = CAST(:__access_user_id AS uuid))
                OR (da.grantee_type = 'group' AND da.grantee_id = ANY(CAST(:__access_group_ids AS uuid[]))))
        )
    )"""


def _access_params(current_user: CurrentUser) -> dict:
    """Params for _access_where_clause — fixed names, safe to merge into
    any query's params dict unconditionally (harmless if unreferenced,
    e.g. for an admin caller where the clause itself is never emitted)."""
    return {
        "__access_user_id":  current_user.id,
        "__access_group_ids": current_user.group_ids or [],
    }


async def _document_visible(conn, table: str, doc_id: str, current_user: CurrentUser) -> bool:
    """Direct existence+visibility check for one document — used wherever
    a single table+id is acted on outside the UNION list/queue queries
    (detail view, links, review actions)."""
    access_clause = _access_where_clause(table, current_user)
    where = "id = CAST(:id AS uuid)"
    if access_clause:
        where += f" AND {access_clause}"
    params = {"id": doc_id, **_access_params(current_user)}
    result = await conn.execute(text(f'SELECT 1 FROM "{table}" WHERE {where}'), params)
    return result.one_or_none() is not None


def _per_table_select(
    table: str,
    cols: dict[str, str],
    *,
    q: Optional[str],
    confidence: Optional[str],
    review_status: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    current_user: CurrentUser,
    reviewed_by: Optional[str] = None,
    series: Optional[str] = None,
) -> Optional[str]:
    """Build the per-table SELECT fragment for the UNION.  Returns None when
    the table should be excluded (e.g. q is set but table has no text cols,
    or reviewed_by is set but the table has never had a reviewed_by column
    added — see patch_review's lazy-ALTER — so it can't have any matches).

    Every branch projects the same fixed shape (id, table_name,
    document_type, confidence, review_status, ingested_at,
    source_image_path, extra_fields) regardless of the table's actual
    schema — extra_fields is a JSONB blob of whatever non-system columns
    that table has, which is what keeps the UNION ALL shape-compatible
    across tenants/document types with entirely different field sets.
    """
    searchable = _searchable_cols(cols)
    if q and not searchable:
        return None
    if reviewed_by and "reviewed_by" not in cols:
        return None

    extra = _extra_cols(cols)
    extra_expr = (
        "jsonb_build_object(" + ", ".join(f"'{c}', {c}" for c in extra) + ")"
        if extra else "'{}'::jsonb"
    )

    sel = [
        "id::text AS id",
        f"'{table}'::text AS table_name",
        "document_type::text AS document_type" if "document_type" in cols else "NULL::text AS document_type",
        "record_id::text AS record_id" if "record_id" in cols else "NULL::text AS record_id",
        "confidence::text    AS confidence",
        "review_status::text AS review_status",
        "ingested_at::text   AS ingested_at",
        "source_image_path",
        "reviewed_by::text AS reviewed_by" if "reviewed_by" in cols else "NULL::text AS reviewed_by",
        "series_id::text AS series_id" if "series_id" in cols else "NULL::text AS series_id",
        f"{extra_expr} AS extra_fields",
    ]

    where: list[str] = []
    if q:
        # Use the GIN index created by _ensure_table for fast FTS on large tables.
        # The expression must match the index expression exactly so PostgreSQL picks it up.
        coalesces = " || ' ' || ".join(f"COALESCE({c},'')" for c in searchable)
        where.append(
            f"to_tsvector('french', {coalesces})"
            f" @@ plainto_tsquery('french', :q)"
        )
    if confidence:
        where.append("confidence = :confidence")
    if review_status:
        where.append("review_status = :review_status")
    if date_from:
        where.append("ingested_at >= CAST(:date_from AS timestamptz)")
    if date_to:
        where.append("ingested_at <= CAST(:date_to AS timestamptz)")
    if reviewed_by:
        where.append("reviewed_by = CAST(:reviewed_by AS uuid)")
    if series:
        where.append("series_id = CAST(:series AS uuid)")
    access_clause = _access_where_clause(table, current_user)
    if access_clause:
        where.append(access_clause)

    where_str = ("WHERE " + " AND ".join(where)) if where else ""
    return f'SELECT {", ".join(sel)}\nFROM "{table}"\n{where_str}'


async def _run_list_query(
    conn,
    tables_cols: dict[str, dict[str, str]],
    *,
    only_table: Optional[str],
    q: Optional[str],
    confidence: Optional[str],
    review_status: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    page: int,
    page_size: int,
    current_user: CurrentUser,
    reviewed_by: Optional[str] = None,
    series: Optional[str] = None,
) -> tuple[list[dict], int]:
    scope = (
        {only_table: tables_cols[only_table]}
        if only_table and only_table in tables_cols
        else tables_cols
    )
    if not scope:
        return [], 0

    parts = [
        _per_table_select(
            tbl, cols,
            q=q, confidence=confidence, review_status=review_status,
            date_from=date_from, date_to=date_to, reviewed_by=reviewed_by,
            series=series,
            current_user=current_user,
        )
        for tbl, cols in scope.items()
    ]
    parts = [p for p in parts if p]
    if not parts:
        return [], 0

    union_sql  = "\nUNION ALL\n".join(parts)
    params: dict = {
        "page_size": page_size, "offset": (page - 1) * page_size,
        **_access_params(current_user),
    }
    if q:
        params["q"] = q  # plainto_tsquery parses the raw search string
    if confidence:
        params["confidence"] = confidence
    if review_status:
        params["review_status"] = review_status
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if reviewed_by:
        params["reviewed_by"] = reviewed_by
    if series:
        params["series"] = series

    sql = f"""
        WITH combined AS (
            {union_sql}
        )
        SELECT *, COUNT(*) OVER() AS total_count
        FROM combined
        ORDER BY ingested_at DESC NULLS LAST
        LIMIT :page_size OFFSET :offset
    """
    result = await conn.execute(text(sql), params)
    rows   = result.mappings().all()
    total  = int(rows[0]["total_count"]) if rows else 0
    return [
        {k: v for k, v in dict(r).items() if k != "total_count"}
        for r in rows
    ], total


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/db", tags=["documents-db"])


@router.get("/types", summary="List document types")
@cache(expire=60, namespace="types", key_builder=_path_key_builder)
async def list_types(current_user: CurrentUser = Depends(get_current_user)):
    """
    Discover all ingest-created tables via information_schema and return
    each table's name, document type label, row count, and last ingestion
    timestamp. Used to populate the type-filter dropdown in the document browser.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if not tables_cols:
            return []

        results = []
        for table in sorted(tables_cols):
            # document_type is a dynamic VLM-derived column, not a
            # guaranteed base one — tables populated only by crashed
            # extractions (empty fields) never get it created.
            doc_type_expr = (
                "MAX(document_type)" if "document_type" in tables_cols[table] else "NULL"
            )
            access_clause = _access_where_clause(table, current_user)
            where_str = f"WHERE {access_clause}" if access_clause else ""
            row = await conn.execute(
                text(
                    f'SELECT COUNT(*), MAX(ingested_at), '
                    f'{doc_type_expr} FROM "{table}" {where_str}'
                ),
                _access_params(current_user) if access_clause else {},
            )
            count, last_ingested, doc_type = row.one()
            results.append(
                {
                    "table_name":    table,
                    "document_type": doc_type or table,
                    "count":         int(count),
                    "last_ingested": (
                        last_ingested.isoformat() if last_ingested else None
                    ),
                }
            )
        return results


@router.get("/reviewers", summary="List reviewers")
@cache(expire=60, namespace="reviewers", key_builder=_path_key_builder)
async def list_reviewers(current_user: CurrentUser = Depends(get_current_user)):
    """
    Every user who has reviewed at least one document in this tenant, with
    their name/email resolved from sdai_users. Powers the reviewer filter
    on the document browser. A table only gets a reviewed_by column once
    its first document is approved/rejected (patch_review's lazy ALTER),
    so tables without one simply contribute no ids here.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        reviewed_tables = [t for t, cols in tables_cols.items() if "reviewed_by" in cols]
        if not reviewed_tables:
            return []

        # UNION (not UNION ALL) already deduplicates ids across tables.
        # A reviewer who has only ever reviewed documents restricted away
        # from the caller doesn't show up here either — even their bare
        # existence in the filter isn't information a non-admin should
        # get from a document they can't otherwise see.
        id_parts = []
        for t in reviewed_tables:
            access_clause = _access_where_clause(t, current_user)
            where = "reviewed_by IS NOT NULL"
            if access_clause:
                where += f" AND {access_clause}"
            id_parts.append(f'SELECT reviewed_by FROM "{t}" WHERE {where}')
        id_rows = await conn.execute(text("\nUNION\n".join(id_parts)), _access_params(current_user))
        ids = [str(row[0]) for row in id_rows]
        if not ids:
            return []

        user_rows = await conn.execute(
            text("SELECT id, email, full_name FROM sdai_users WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        results = [
            {"id": str(r[0]), "email": r[1], "full_name": r[2]}
            for r in user_rows
        ]
        results.sort(key=lambda r: (r["full_name"] or r["email"]).lower())
        return results


@router.get("/documents")
@limiter.limit("120/minute")
async def list_documents(
    request:       Request,
    page:          int           = Query(1,  ge=1),
    page_size:     int           = Query(20, ge=1, le=100),
    document_type: Optional[str] = None,
    confidence:    Optional[str] = None,
    review_status: Optional[str] = None,
    date_from:     Optional[str] = None,
    date_to:       Optional[str] = None,
    q:             Optional[str] = None,
    reviewed_by:   Optional[str] = None,
    series:        Optional[str] = None,
    current_user:  CurrentUser   = Depends(get_current_user),
):
    """
    Paginated document list.
    - document_type: restrict to a single table (sanitized → table name)
    - q: full-text search (GIN index) across every TEXT field the table has
    - confidence / review_status: equality filter
    - date_from / date_to: filter on ingested_at (ISO 8601 dates)
    - reviewed_by: restrict to documents reviewed by this user id
    - series: restrict to documents assigned to this series id
    """
    only_table = _sanitize(document_type) if document_type else None

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)

        if only_table and only_table not in tables_cols:
            return {"total": 0, "page": page, "page_size": page_size, "results": []}

        results, total = await _run_list_query(
            conn, tables_cols,
            only_table=only_table,
            q=q,
            confidence=confidence,
            review_status=review_status,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
            reviewed_by=reviewed_by,
            series=series,
            current_user=current_user,
        )

    return {"total": total, "page": page, "page_size": page_size, "results": results}


def _serialize_row(row) -> dict:
    """JSON-safe dict from a full-row Mapping — dates as ISO strings,
    JSONB columns left as-is (FastAPI serializes them natively), everything
    else via str(). Shared by get_document_detail and
    update_document_fields, which both return a full row this way."""
    data: dict = {}
    for k, v in dict(row).items():
        if v is None:
            data[k] = None
        elif hasattr(v, "isoformat"):
            data[k] = v.isoformat()
        elif isinstance(v, (int, float, bool)):
            data[k] = v
        elif isinstance(v, (list, dict)):
            data[k] = v  # JSONB — let FastAPI serialize as proper JSON
        else:
            data[k] = str(v)
    return data


@router.get("/documents/{table_name}/{doc_id}", summary="Document detail")
async def get_document_detail(
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return every column value for one document.
    The table name is validated against information_schema before querying.
    JSONB columns (arrays) are returned as proper JSON arrays.
    Use this endpoint to populate the review form and the document detail page.
    """
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))

        access_clause = _access_where_clause(safe, current_user)
        where = "id = CAST(:id AS uuid)"
        if access_clause:
            where += f" AND {access_clause}"
        result = await conn.execute(
            text(f'SELECT * FROM "{safe}" WHERE {where}'),
            {"id": doc_id, **_access_params(current_user)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    return _serialize_row(row)


class UpdateDocumentFieldsBody(BaseModel):
    fields: dict


@router.patch("/documents/{table_name}/{doc_id}/fields", summary="Edit extraction field data")
async def update_document_fields(
    table_name:   str,
    doc_id:       str,
    body:         UpdateDocumentFieldsBody,
    current_user: CurrentUser = Depends(require_extraction_editor),
):
    """Correct extracted field values on a document regardless of its
    review status — including one that's already been approved and filed.
    Requires the extraction-editing permission (admin, or a reviewer
    delegated can_edit_extraction). Distinct from patch_review's approve
    action: this never touches review_status/confidence/reviewed_by —
    it's a pure content correction, not a review-workflow transition, and
    it's not available anywhere in the read-only document browser/detail
    pages for anyone without that permission."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        cols = tables_cols[safe]
        editable, set_parts, params = _build_field_set_clause(cols, body.fields)
        if not editable:
            raise HTTPException(status_code=422, detail=t("documents.no_editable_fields", current_user.locale))

        params["id"] = doc_id
        result = await conn.execute(
            text(f'UPDATE "{safe}" SET {", ".join(set_parts)} WHERE id = CAST(:id AS uuid) RETURNING *'),
            params,
        )
        row = result.mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

    await log_action(
        action="document_fields_edited",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"fields_changed": list(editable.keys())},
    )

    return _serialize_row(row)


# ── Document links (chain-of-custody) ─────────────────────────────────────────

class CreateLinkBody(BaseModel):
    to_table: str
    to_id:    str
    relation: str = Field(..., min_length=1, max_length=100)
    note:     Optional[str] = None


async def _delete_links_for_document(conn, tenant_id: str, table_name: str, doc_id: str) -> int:
    """Delete every link (either direction) referencing one document.
    Called from both hard-delete paths (review_router.delete_document and
    ingest_router._delete_document_by_batch_document_id) in the same
    transaction as the document delete, so a link never outlives the
    document it points at."""
    result = await conn.execute(
        text("""
            DELETE FROM sdai_document_links
            WHERE tenant_id = CAST(:tid AS uuid)
              AND ((from_table = :t AND from_id = CAST(:id AS uuid))
                OR (to_table   = :t AND to_id   = CAST(:id AS uuid)))
        """),
        {"tid": tenant_id, "t": table_name, "id": doc_id},
    )
    return result.rowcount


async def _enrich_links(conn, current_user: CurrentUser, table_name: str, doc_id: str) -> list[dict]:
    """Fetch every link involving (table_name, doc_id) and attach enough
    info about the *other* document (type, a display value, image path)
    for the frontend to render something useful without a second
    round-trip per link. Batched per distinct other-table, not per-link.
    An "other" document current_user isn't allowed to see is simply
    excluded from the batch fetch below, which the existing
    other_docs.get(...) is None fallback already turns into
    other_missing: true — no separate redaction logic needed."""
    rows = (await conn.execute(
        text("""
            SELECT id, from_table, from_id, to_table, to_id, relation, note,
                   created_by, created_at
            FROM sdai_document_links
            WHERE tenant_id = CAST(:tid AS uuid)
              AND ((from_table = :t AND from_id = CAST(:id AS uuid))
                OR (to_table   = :t AND to_id   = CAST(:id AS uuid)))
            ORDER BY created_at DESC
        """),
        {"tid": current_user.tenant_id, "t": table_name, "id": doc_id},
    )).mappings().all()

    if not rows:
        return []

    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)

    # (other_table -> {other_id -> row}) fetched once per distinct table.
    other_ids_by_table: dict[str, set[str]] = {}
    for r in rows:
        outgoing = r["from_table"] == table_name and str(r["from_id"]) == doc_id
        other_table = r["to_table"] if outgoing else r["from_table"]
        other_id    = str(r["to_id"] if outgoing else r["from_id"])
        other_ids_by_table.setdefault(other_table, set()).add(other_id)

    other_docs: dict[tuple[str, str], dict] = {}
    for other_table, ids in other_ids_by_table.items():
        if other_table not in tables_cols:
            continue  # table gone (shouldn't happen given cascade-delete; degrade gracefully)
        display_col = _display_col(tables_cols[other_table])
        display_expr = f"{display_col}::text" if display_col else "NULL::text"
        access_clause = _access_where_clause(other_table, current_user)
        where = "id = ANY(:ids)"
        if access_clause:
            where += f" AND {access_clause}"
        other_rows = (await conn.execute(
            text(
                f'SELECT id::text AS id, document_type, source_image_path,'
                f' {display_expr} AS display'
                f' FROM "{other_table}" WHERE {where}'
            ),
            {"ids": list(ids), **_access_params(current_user)},
        )).mappings().all()
        for r in other_rows:
            other_docs[(other_table, r["id"])] = dict(r)

    links = []
    for r in rows:
        outgoing = r["from_table"] == table_name and str(r["from_id"]) == doc_id
        other_table = r["to_table"] if outgoing else r["from_table"]
        other_id    = str(r["to_id"] if outgoing else r["from_id"])
        other = other_docs.get((other_table, other_id))
        links.append({
            "id":                      str(r["id"]),
            "relation":                r["relation"],
            "note":                    r["note"],
            "created_at":              r["created_at"].isoformat() if r["created_at"] else None,
            "created_by":              str(r["created_by"]) if r["created_by"] else None,
            "direction":               "outgoing" if outgoing else "incoming",
            "other_table":             other_table,
            "other_id":                other_id,
            "other_document_type":     other["document_type"] if other else None,
            "other_display":           other["display"] if other else None,
            "other_source_image_path": other["source_image_path"] if other else None,
            "other_missing":           other is None,
        })
    return links


@router.get("/documents/{table_name}/{doc_id}/links", summary="Document links")
async def get_document_links(
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return every chain-of-custody link involving this document, in
    either direction, enriched with basic info about the linked document."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        links = await _enrich_links(conn, current_user, safe, doc_id)

    return {"links": links}


@router.post("/documents/{table_name}/{doc_id}/links", summary="Link to another document")
@limiter.limit("60/minute")
async def create_document_link(
    request:      Request,
    table_name:   str,
    doc_id:       str,
    body:         CreateLinkBody,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a chain-of-custody link from this document to another (e.g.
    a sale deed 'concerns' the original title deed). Both documents must
    belong to the caller's own tenant — this check IS the tenant-isolation
    boundary for this feature, since a real FK against a dynamically-named
    table isn't possible. Idempotent: an identical (from, to, relation)
    link already existing returns the existing row rather than erroring."""
    safe    = _sanitize(table_name)
    to_safe = _sanitize(body.to_table)

    if safe == to_safe and doc_id == body.to_id:
        raise HTTPException(status_code=400, detail=t("documents.cannot_link_self", current_user.locale))

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if to_safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=to_safe))

        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))
        if not await _document_visible(conn, to_safe, body.to_id, current_user):
            raise HTTPException(status_code=404, detail=t("documents.target_document_not_found", current_user.locale))

        result = await conn.execute(
            text("""
                INSERT INTO sdai_document_links
                    (tenant_id, from_table, from_id, to_table, to_id, relation, note, created_by)
                VALUES
                    (CAST(:tid AS uuid), :ft, CAST(:fid AS uuid), :tt, CAST(:tidoc AS uuid), :rel, :note, CAST(:cb AS uuid))
                ON CONFLICT (tenant_id, from_table, from_id, to_table, to_id, relation) DO NOTHING
                RETURNING id
            """),
            {
                "tid": current_user.tenant_id, "ft": safe, "fid": doc_id,
                "tt": to_safe, "tidoc": body.to_id, "rel": body.relation,
                "note": body.note, "cb": current_user.id,
            },
        )
        link_id = result.scalar()
        if link_id is None:
            existing = await conn.execute(
                text("""
                    SELECT id FROM sdai_document_links
                    WHERE tenant_id = CAST(:tid AS uuid) AND from_table = :ft AND from_id = CAST(:fid AS uuid)
                      AND to_table = :tt AND to_id = CAST(:tidoc AS uuid) AND relation = :rel
                """),
                {
                    "tid": current_user.tenant_id, "ft": safe, "fid": doc_id,
                    "tt": to_safe, "tidoc": body.to_id, "rel": body.relation,
                },
            )
            link_id = existing.scalar()

    await log_action(
        action="document_linked",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"to_table": to_safe, "to_id": body.to_id, "relation": body.relation},
        ip_address=client_ip(request),
    )

    return {"id": str(link_id)}


@router.delete("/links/{link_id}", summary="Remove a document link")
@limiter.limit("60/minute")
async def delete_document_link(
    request:      Request,
    link_id:      str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a chain-of-custody link. Any tenant user may do this (not
    admin-only) — removing a link only retracts an assertion between two
    still-intact documents, no extracted data is destroyed, and it's
    trivially re-creatable; this matches flag/approve/reject's permission
    level, not hard-delete's. Requires the caller to be able to see at
    least one side of the link — this endpoint only has a link_id, not a
    table+id the way create/list do, so there's no single "the document
    you're viewing" to check visibility against."""
    async with _engine().begin() as conn:
        link_row = await conn.execute(
            text("""
                SELECT from_table, from_id, to_table, to_id, relation
                FROM sdai_document_links
                WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
            """),
            {"id": link_id, "tid": current_user.tenant_id},
        )
        link = link_row.mappings().one_or_none()
        if link is None:
            raise HTTPException(status_code=404, detail=t("common.link_not_found", current_user.locale))

        can_see_from = await _document_visible(conn, link["from_table"], str(link["from_id"]), current_user)
        can_see_to   = await _document_visible(conn, link["to_table"], str(link["to_id"]), current_user)
        if not (can_see_from or can_see_to):
            raise HTTPException(status_code=404, detail=t("common.link_not_found", current_user.locale))

        result = await conn.execute(
            text("DELETE FROM sdai_document_links WHERE id = CAST(:id AS uuid) RETURNING from_table, from_id, to_table, to_id, relation"),
            {"id": link_id},
        )
        row = result.mappings().one_or_none()

    await log_action(
        action="document_link_removed",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "from_table": row["from_table"], "from_id": str(row["from_id"]),
            "to_table": row["to_table"], "to_id": str(row["to_id"]),
            "relation": row["relation"],
        },
        ip_address=client_ip(request),
    )

    return {"ok": True}


# ── Document access grants ─────────────────────────────────────────────────────

@router.get("/grantees", summary="Groups and users available to tag a document with")
async def list_grantees(current_user: CurrentUser = Depends(require_access_manager)):
    """Minimal {id, name}/{id, email, full_name} listing for the access-grant
    picker — deliberately not the same data as GET /api/admin/groups or
    GET /api/admin/users (roles, can_manage_access flags, member counts),
    which stay admin-only. A can_manage_access reviewer can tag a document
    without needing that broader admin view, so this is scoped to exactly
    what the picker needs and gated by require_access_manager instead."""
    async with _engine().connect() as conn:
        group_rows = await conn.execute(
            text("SELECT id, name FROM sdai_groups WHERE tenant_id = CAST(:tid AS uuid) ORDER BY name"),
            {"tid": current_user.tenant_id},
        )
        user_rows = await conn.execute(
            text("""
                SELECT id, email, full_name FROM sdai_users
                WHERE tenant_id = CAST(:tid AS uuid)
                ORDER BY COALESCE(full_name, email)
            """),
            {"tid": current_user.tenant_id},
        )
        return {
            "groups": [{"id": str(r[0]), "name": r[1]} for r in group_rows],
            "users": [{"id": str(r[0]), "email": r[1], "full_name": r[2]} for r in user_rows],
        }


class CreateAccessGrantBody(BaseModel):
    grantee_type: str = Field(..., pattern="^(group|user)$")
    grantee_id:   str


@router.get("/documents/{table_name}/{doc_id}/access", summary="List access grants")
async def get_document_access(
    table_name:   str,
    doc_id:       str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Who can see this document, beyond admins and (if it has no grants
    at all) everyone in the tenant. Visible read-only to anyone who can
    already see the document — editing is gated separately, see POST/DELETE
    below."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        rows = (await conn.execute(
            text("""
                SELECT id, grantee_type, grantee_id, granted_by, granted_at
                FROM sdai_document_access
                WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t AND document_id = CAST(:id AS uuid)
                ORDER BY granted_at
            """),
            {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
        )).mappings().all()

        group_ids = [str(r["grantee_id"]) for r in rows if r["grantee_type"] == "group"]
        user_ids  = [str(r["grantee_id"]) for r in rows if r["grantee_type"] == "user"]
        group_names: dict[str, str] = {}
        user_names: dict[str, str] = {}
        if group_ids:
            g_rows = await conn.execute(
                text("SELECT id, name FROM sdai_groups WHERE id = ANY(:ids)"), {"ids": group_ids}
            )
            group_names = {str(r[0]): r[1] for r in g_rows}
        if user_ids:
            u_rows = await conn.execute(
                text("SELECT id, email, full_name FROM sdai_users WHERE id = ANY(:ids)"), {"ids": user_ids}
            )
            user_names = {str(r[0]): (r[2] or r[1]) for r in u_rows}

    grants = []
    for r in rows:
        gid = str(r["grantee_id"])
        name = group_names.get(gid) if r["grantee_type"] == "group" else user_names.get(gid)
        grants.append({
            "id":            str(r["id"]),
            "grantee_type":  r["grantee_type"],
            "grantee_id":    gid,
            "grantee_name":  name or "(removed)",
            "granted_by":    str(r["granted_by"]) if r["granted_by"] else None,
            "granted_at":    r["granted_at"].isoformat() if r["granted_at"] else None,
        })
    return {"grants": grants, "can_manage": current_user.can_manage_document_access}


@router.post("/documents/{table_name}/{doc_id}/access", summary="Grant access to a document")
async def create_document_access(
    table_name:   str,
    doc_id:       str,
    body:         CreateAccessGrantBody,
    current_user: CurrentUser = Depends(require_access_manager),
):
    """Restrict a document by granting a group or a specific person
    access to it. The first grant on a previously-open document is what
    makes it restricted at all — see sdai_document_access's docstring in
    ingest_router._INIT_DDL."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        # can_manage_access still can't grant access to a document they
        # can't already see (an admin bypasses this, same as everywhere else).
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        if body.grantee_type == "group":
            grantee_row = await conn.execute(
                text("SELECT 1 FROM sdai_groups WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
                {"id": body.grantee_id, "tid": current_user.tenant_id},
            )
        else:
            grantee_row = await conn.execute(
                text("SELECT 1 FROM sdai_users WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
                {"id": body.grantee_id, "tid": current_user.tenant_id},
            )
        if grantee_row.one_or_none() is None:
            raise HTTPException(status_code=404, detail=t("common.group_not_found" if body.grantee_type == "group" else "common.user_not_found", current_user.locale))

        result = await conn.execute(
            text("""
                INSERT INTO sdai_document_access
                    (tenant_id, table_name, document_id, grantee_type, grantee_id, granted_by)
                VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), :gt, CAST(:gid AS uuid), CAST(:by AS uuid))
                ON CONFLICT (tenant_id, table_name, document_id, grantee_type, grantee_id) DO NOTHING
                RETURNING id
            """),
            {
                "tid": current_user.tenant_id, "t": safe, "id": doc_id,
                "gt": body.grantee_type, "gid": body.grantee_id, "by": current_user.id,
            },
        )
        grant_id = result.scalar()
        if grant_id is None:
            existing = await conn.execute(
                text("""
                    SELECT id FROM sdai_document_access
                    WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t AND document_id = CAST(:id AS uuid)
                      AND grantee_type = :gt AND grantee_id = CAST(:gid AS uuid)
                """),
                {
                    "tid": current_user.tenant_id, "t": safe, "id": doc_id,
                    "gt": body.grantee_type, "gid": body.grantee_id,
                },
            )
            grant_id = existing.scalar()

    await log_action(
        action="document_access_granted",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"grantee_type": body.grantee_type, "grantee_id": body.grantee_id},
    )
    return {"id": str(grant_id)}


@router.delete("/access/{grant_id}", summary="Remove an access grant")
async def delete_document_access(
    grant_id:     str,
    current_user: CurrentUser = Depends(require_access_manager),
):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("""
                DELETE FROM sdai_document_access
                WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
                RETURNING table_name, document_id, grantee_type, grantee_id
            """),
            {"id": grant_id, "tid": current_user.tenant_id},
        )
        row = result.mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=t("common.grant_not_found", current_user.locale))

    await log_action(
        action="document_access_revoked",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=row["table_name"],
        document_id=str(row["document_id"]),
        details={"grantee_type": row["grantee_type"], "grantee_id": str(row["grantee_id"])},
    )
    return {"ok": True}


# ── Fonds/series hierarchy ──────────────────────────────────────────────────
# One document belongs to at most one series (the standard archival model,
# not many-to-many). Series creation is admin-only (structural, like
# groups); assigning an existing series to a document is open to any
# tenant user — organizational, not a security control.

class CreateSeriesBody(BaseModel):
    name:        str           = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class AssignSeriesBody(BaseModel):
    series_id: Optional[str] = None


@router.get("/series", summary="List series")
async def list_series(current_user: CurrentUser = Depends(get_current_user)):
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT id, name, description, created_at
                FROM sdai_series
                WHERE tenant_id = CAST(:tid AS uuid)
                ORDER BY name
            """),
            {"tid": current_user.tenant_id},
        )
        return [
            {
                "id": str(r[0]), "name": r[1], "description": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]


@router.post("/series", summary="Create a series")
async def create_series(body: CreateSeriesBody, current_user: CurrentUser = Depends(require_admin)):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO sdai_series (tenant_id, name, description, created_by)
                VALUES (CAST(:tid AS uuid), :name, :description, CAST(:cb AS uuid))
                ON CONFLICT (tenant_id, name) DO NOTHING
                RETURNING id
            """),
            {
                "tid": current_user.tenant_id, "name": body.name,
                "description": body.description, "cb": current_user.id,
            },
        )
        series_id = result.scalar()
        if series_id is None:
            raise HTTPException(status_code=409, detail=t("common.series_already_exists", current_user.locale, name=body.name))

    await log_action(
        action="series_created",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"series_id": str(series_id), "name": body.name},
    )
    return {"id": str(series_id), "name": body.name}


@router.delete("/series/{series_id}", summary="Delete a series")
async def delete_series(series_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Documents keep their series_id pointing at a now-nonexistent row —
    no FK from the dynamic tables to sdai_series (there are too many of
    them, per document type, to maintain one) — so the frontend treats an
    unresolvable series_id the same as "no series", matching how a removed
    access grant's grantee is already handled."""
    async with _engine().begin() as conn:
        result = await conn.execute(
            text("DELETE FROM sdai_series WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) RETURNING id"),
            {"id": series_id, "tid": current_user.tenant_id},
        )
        if result.one_or_none() is None:
            raise HTTPException(status_code=404, detail=t("common.series_not_found", current_user.locale))
    return {"ok": True}


@router.patch("/documents/{table_name}/{doc_id}/series", summary="Assign a document to a series")
async def assign_document_series(
    table_name:   str,
    doc_id:       str,
    body:         AssignSeriesBody,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Open to any tenant user who can already see the document —
    organizational filing, not a security control (unlike access grants,
    gated behind require_access_manager)."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe))
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))

        if body.series_id is not None:
            series_row = await conn.execute(
                text("SELECT 1 FROM sdai_series WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
                {"id": body.series_id, "tid": current_user.tenant_id},
            )
            if series_row.one_or_none() is None:
                raise HTTPException(status_code=404, detail=t("common.series_not_found", current_user.locale))

        await conn.execute(
            text(f'UPDATE "{safe}" SET series_id = CAST(:sid AS uuid) WHERE id = CAST(:id AS uuid)'),
            {"sid": body.series_id, "id": doc_id},
        )

    await log_action(
        action="series_assigned",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"series_id": body.series_id},
    )
    return {"ok": True}


@router.get("/schema")
@limiter.limit("30/minute")
@cache(expire=30, namespace="schema", key_builder=_path_key_builder)
async def get_schema(request: Request, current_user: CurrentUser = Depends(get_current_user)):
    """
    Return full schema: tables, columns (with type/nullable), row counts,
    last ingested, and foreign key relationships.
    Everything discovered from information_schema — no table names hardcoded.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if not tables_cols:
            return {"tables": []}

        # Fetch column details for all ingest tables in one query
        col_result = await conn.execute(text("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """), {"tables": list(tables_cols.keys())})

        table_columns: dict[str, list[dict]] = {}
        for row in col_result:
            table_columns.setdefault(row[0], []).append({
                "name":     row[1],
                "type":     row[2],
                "nullable": row[3] == "YES",
            })

        # Fetch foreign keys for all ingest tables in one query
        fk_result = await conn.execute(text("""
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name  AS references_table,
                ccu.column_name AS references_column
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
               AND ccu.table_schema   = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema    = 'public'
              AND tc.table_name      = ANY(:tables)
        """), {"tables": list(tables_cols.keys())})

        table_fks: dict[str, list[dict]] = {}
        for row in fk_result:
            table_fks.setdefault(row[0], []).append({
                "column":            row[1],
                "references_table":  row[2],
                "references_column": row[3],
            })

        results = []
        for table_name in sorted(tables_cols):
            # document_type is a dynamic VLM-derived column, not a
            # guaranteed base one — tables populated only by crashed
            # extractions (empty fields) never get it created.
            has_doc_type  = any(
                c["name"] == "document_type" for c in table_columns.get(table_name, [])
            )
            doc_type_expr = "MAX(document_type)" if has_doc_type else "NULL"
            access_clause = _access_where_clause(table_name, current_user)
            where_str = f"WHERE {access_clause}" if access_clause else ""
            stat = await conn.execute(
                text(f'SELECT COUNT(*), MAX(ingested_at), {doc_type_expr} FROM "{table_name}" {where_str}'),
                _access_params(current_user) if access_clause else {},
            )
            count, last_ingested, doc_type = stat.one()
            results.append({
                "name":          table_name,
                "document_type": doc_type or table_name,
                "row_count":     int(count),
                "last_ingested": last_ingested.isoformat() if last_ingested else None,
                "columns":       table_columns.get(table_name, []),
                "foreign_keys":  table_fks.get(table_name, []),
            })

    return {"tables": results}


@router.get("/image")
async def serve_processed_image(
    path:         str,
    current_user: str = Depends(get_current_user),
):
    """
    Serve a document-pipeline file by absolute path — a processed page
    image, an original uploaded PDF, or a source file ingested via a
    server path or Google Drive.
    Validates that the requested path resolves within one of the known
    ingest directories to prevent path-traversal attacks.
    """
    file_path = Path(path).resolve()
    if not any(file_path.is_relative_to(root) for root in _SAFE_FILE_ROOTS):
        raise HTTPException(status_code=403, detail=t("common.access_denied", current_user.locale))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=t("common.file_not_found", current_user.locale))
    return FileResponse(file_path)
