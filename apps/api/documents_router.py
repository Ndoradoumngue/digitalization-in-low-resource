"""
Document browser - queries the dynamically-inferred PostgreSQL tables created
by the ingestion pipeline.  Table names are always discovered via
information_schema; they are never hardcoded or taken raw from user input.

Endpoints:
  GET    /api/db/documents                    - paginated list with filters & full-text search
  GET    /api/db/documents/{tbl}/{id}         - full field detail for one document
  PATCH  /api/db/documents/{tbl}/{id}/fields  - edit extraction field data (permission-gated)
  GET    /api/db/documents/{tbl}/{id}/links   - chain-of-custody links for one document
  POST   /api/db/documents/{tbl}/{id}/links   - link this document to another
  DELETE /api/db/links/{link_id}              - remove a link
  GET    /api/db/documents/{tbl}/{id}/access  - access grants on one document
  POST   /api/db/documents/{tbl}/{id}/access  - grant a group/user access (restricts it)
  PATCH  /api/db/access/{grant_id}            - change a grant's permission level
  DELETE /api/db/access/{grant_id}            - remove an access grant (any scope)
  PATCH  /api/db/documents/{tbl}/{id}/confidentiality - classify public..secret (access managers)
  GET    /api/db/grants?scope=series|doc_type - series-/type-level grants (admin-only)
  POST   /api/db/grants                       - grant on a whole series or type (admin-only)
  GET    /api/db/grantees                     - groups/users available to tag a document with
  GET    /api/db/series                       - list this tenant's series
  POST   /api/db/series                       - create a series (admin-only)
  DELETE /api/db/series/{id}                  - delete a series (admin-only)
  PATCH  /api/db/documents/{tbl}/{id}/series   - assign/unassign a document's series
  GET    /api/db/types                        - table inventory with counts
  GET    /api/db/reviewers                    - users who have reviewed at least one document
  GET    /api/db/image                        - serve a processed image, original PDF,
                                                 or path/Drive-ingested source file
"""

import json
import os
import re
from pathlib import Path

from audit import client_ip, log_action
from auth import (
    CurrentUser,
    require_access_manager,
    require_admin,
    require_extraction_editor,
    require_reviewer,
    require_user,
)
from cache import _path_key_builder, first_in_window, invalidate_cache
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from datetime import datetime, timezone

from export_format import insert_statement, list_to_csv, serial
from fastapi.responses import FileResponse, Response
from fastapi_cache.decorator import cache
from history import field_changes, record_field_changes
from i18n import t
from ingest_router import DOCS_DIR as _INGEST_DOCS_DIR
from ingest_router import UPLOADS_DIR as _INGEST_UPLOADS_DIR
from ingest_router import _GED_COL_NAMES
from ingest_router import _engine  # lazy accessor - None until startup()
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text
from sync import files as sync_files

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

# Directories the /image endpoint is allowed to serve files from -
# processed page images, original uploads (incl. source PDFs), and files
# ingested via a server path or Google Drive.
# Processed page images - every other servable file is an uploaded or
# ingested original (see serve_processed_image's download audit).
_IMAGES_ROOT = (DATA_DIR / "images").resolve()
_SAFE_FILE_ROOTS = [
    _IMAGES_ROOT,
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

    Uses starts_with(), not LIKE ... || '%' - LIKE's '_' wildcard would
    let e.g. tenant 'land' match a different tenant's table like
    't_landowner_...'. This one function is what tenant-scopes nearly
    every endpoint in this module and in review_router.py, since both
    route all table access through it before touching any table by name.

    Carries each column's Postgres data_type (not just its name) so callers
    can classify columns (e.g. "is this TEXT and therefore searchable?")
    without a second information_schema round trip - see _searchable_cols/
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

# System/bookkeeping columns every ingest table has - never part of a
# document's extracted, tenant-schema-specific field set. Kept in sync with
# ingest_router._ensure_table's base_cols/reviewed_at/reviewed_by columns.
_BASE_COLS = frozenset(
    {
        "id",
        "source_image_path",
        "source_pdf_path",
        "page_image_paths",
        "batch_id",
        "batch_document_id",
        "ingested_at",
        "confidence",
        "review_status",
        "content_hash",
        "reviewed_at",
        "reviewed_by",
        "series_id",
        "uploaded_by",
    }
    | _GED_COL_NAMES
)

# Columns an extraction-editor is never allowed to overwrite directly -
# shared between review_router.patch_review's approve action and
# update_document_fields below, so the two field-editing entry points
# can't drift apart on what's off-limits.
_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "ingested_at",
        "review_status",
        "source_image_path",
        "table_name",
        "batch_id",
    }
    # Lifecycle, confidentiality, versioning, lock and sync state each have
    # their own dedicated, permission-checked actions - never a field edit.
    | _GED_COL_NAMES
)


def _build_field_set_clause(
    cols: dict[str, str],
    fields: dict,
    protected: frozenset[str] = _PROTECTED_FIELDS,
) -> tuple[dict, list[str], dict]:
    """Column-type-aware SET-clause builder for editing extraction field
    values. Returns (editable, set_parts, params): `editable` is the
    submitted fields filtered down to real, non-protected columns (empty
    means nothing there was actually an edit - useful for permission
    checks that only apply when real changes are being made); `set_parts`
    /`params` are ready to merge into an UPDATE's SET clause and bind
    params (params keyed v_<col>, no collision with a caller's own keys)."""
    editable = {k: v for k, v in fields.items() if k in cols and k not in protected}
    set_parts: list[str] = []
    params: dict = {}
    for col, val in editable.items():
        pname = f"v_{col}"
        col_type = cols.get(col, "text")
        is_jsonb = col_type in ("json", "jsonb")
        if val is None or val == "":
            set_parts.append(f'"{col}" = :{pname}')
            params[pname] = None
        elif is_jsonb and isinstance(val, (list, dict)):
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


async def _row_for_update(conn, table: str, doc_id: str) -> dict | None:
    """The document's current row, locked until the caller's transaction
    ends - the "before" side of a field edit's history."""
    row = (
        await conn.execute(
            text(f'SELECT * FROM "{table}" WHERE id = CAST(:id AS uuid) FOR UPDATE'), {"id": doc_id}
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _record_edit_history(
    conn,
    current_user: CurrentUser,
    table: str,
    doc_id: str,
    old: dict | None,
    new,
    fields,
    source: str,
) -> dict:
    """Write sdai_field_history rows for what an edit actually changed, in
    the caller's transaction, and return those changes ({field: {old,
    new}}) for the audit entry. Shared by update_document_fields and
    review_router.patch_review."""
    if old is None or new is None:
        return {}
    changes = field_changes(old, new, fields)
    await record_field_changes(
        conn,
        tenant_id=current_user.tenant_id,
        table_name=table,
        document_id=doc_id,
        changes=changes,
        changed_by=current_user.id,
        source=source,
        version_no=new.get("current_version"),
    )
    return changes


def _searchable_cols(cols: dict[str, str]) -> list[str]:
    """TEXT-typed, non-system columns - eligible for full-text search.
    document_type is intentionally included (unlike the old hardcoded
    field list) - it's just another VLM-extracted TEXT column, and
    matching on it is a harmless, minor search improvement."""
    return [c for c, dtype in cols.items() if dtype == "text" and c not in _BASE_COLS]


def _extra_cols(cols: dict[str, str]) -> list[str]:
    """All non-system columns except document_type and record_id (which
    get their own dedicated projected columns) - the key set for the
    extra_fields blob."""
    return [c for c in cols if c not in _BASE_COLS and c not in ("document_type", "record_id")]


def _display_col(cols: dict[str, str]) -> str | None:
    """Best single column to show as a document's "title" in a link/search
    summary - prefers reference_number for familiarity, else the first
    searchable column alphabetically. None if the table has no TEXT field
    at all (e.g. an all-JSONB schema)."""
    searchable = _searchable_cols(cols)
    if "reference_number" in searchable:
        return "reference_number"
    return sorted(searchable)[0] if searchable else None


# ── Document-level access control ─────────────────────────────────────────────
# A grant (sdai_document_access) targets one document, one series or one
# document type, and carries a cumulative permission level (read <
# download < edit). A document no grant targets at any scope is open to
# its whole tenant, as before. A document some grant targets is restricted
# to admins plus the users/groups holding a targeting grant - at the level
# that grant gives. On top of that, a document's confidentiality must not
# exceed the user's clearance, grant or not, and a role caps the level a
# user can ever reach (reader: download). Admins see every live row. See
# CONTEXT.md "Access Control".

# Cached endpoints whose answer depends on what the caller can see - cleared
# whenever a grant, a confidentiality level or a series assignment changes.
_ACCESS_CACHE_NAMESPACES = ("types", "reviewers", "review", "schema")

# Cumulative permission levels, lowest first.
PERMISSION_LEVELS = ("read", "download", "edit")

# Document confidentiality levels / user clearances, lowest first.
CONFIDENTIALITY_LEVELS = ("public", "internal", "confidential", "secret")

# Highest permission level each non-admin role can reach on any document.
_ROLE_MAX_PERMISSION = {"reviewer": "edit", "reader": "download"}


def _live_where_clause(table: str) -> str:
    """Boolean SQL expression that's true iff a row of `table` is neither
    in the trash (deleted_at) nor destroyed (lifecycle_status) - see
    ingest_router._GED_COLS. Folded into _access_where_clause so every
    user-facing query hides those rows by default, including for admins;
    only an explicit trash/tombstone view should ever opt out."""
    return f"(\"{table}\".deleted_at IS NULL AND \"{table}\".lifecycle_status IS DISTINCT FROM 'destroyed')"


def _sql_list(values) -> str:
    """SQL literal list from internal constants only (never user input)."""
    return ", ".join(f"'{v}'" for v in values)


def _access_where_clause(
    table: str,
    current_user: CurrentUser,
    permission: str = "read",
    *,
    include_deleted: bool = False,
) -> str:
    """Boolean SQL expression, safe to AND into any WHERE clause (or use
    standalone) selecting from `table`, that's true iff current_user holds
    at least `permission` (read/download/edit) on a given row:

        live  AND  (admin  OR  (confidentiality <= clearance
                               AND role allows `permission`
                               AND (no grant targets the row at any scope
                                    OR a grant >= `permission` targets it
                                       for the user or one of their groups)))

    `live` (see _live_where_clause) is skipped with include_deleted=True,
    which then gives admins "" - callers should skip appending it when
    empty rather than adding a redundant "AND ''"/"AND TRUE". A role that
    can never reach `permission` (a reader asking for edit, an API key)
    gets "FALSE". `table` is embedded as a literal - always an
    internally-generated sanitized identifier here, never raw user input,
    matching how table names are already embedded elsewhere in this module
    (e.g. `FROM "{table}"`), and every other literal comes from the
    constant tuples above. Callers MUST also merge
    _access_params(current_user) into the query's params dict whenever
    this returns non-empty."""
    live = "" if include_deleted else _live_where_clause(table)
    if current_user.role == "admin":
        return live
    if permission not in PERMISSION_LEVELS:
        raise ValueError(f"unknown permission level {permission!r}")
    role_max = _ROLE_MAX_PERMISSION.get(current_user.role)
    if role_max is None or PERMISSION_LEVELS.index(permission) > PERMISSION_LEVELS.index(role_max):
        return "FALSE"

    clearance = (
        current_user.clearance if current_user.clearance in CONFIDENTIALITY_LEVELS else "public"
    )
    allowed_levels = CONFIDENTIALITY_LEVELS[: CONFIDENTIALITY_LEVELS.index(clearance) + 1]
    satisfying = PERMISSION_LEVELS[PERMISSION_LEVELS.index(permission) :]

    # The correlated references below MUST be qualified - sdai_document_access
    # has its own `id` and `series_id` columns, so a bare `id` resolves to
    # the grant's own column instead of correlating to the outer document
    # row, silently turning both branches false (NOT EXISTS always true,
    # i.e. no restriction ever takes effect). `"{table}".id` uses the
    # double-quoted table name as the FROM clause's implicit alias, which
    # every caller relies on (none of them alias the outer table explicitly).
    doc = f'"{table}"'
    targets = f"""da.tenant_id = CAST(:__access_tenant_id AS uuid)
              AND ((da.scope = 'document' AND da.table_name = '{table}' AND da.document_id = {doc}.id)
                OR (da.scope = 'series' AND da.series_id = {doc}.series_id)
                OR (da.scope = 'doc_type' AND da.table_name = '{table}'))"""
    access = f"""(
        COALESCE({doc}.confidentiality, 'internal') IN ({_sql_list(allowed_levels)})
        AND (
            NOT EXISTS (SELECT 1 FROM sdai_document_access da WHERE {targets})
            OR EXISTS (
                SELECT 1 FROM sdai_document_access da
                WHERE {targets}
                  AND da.permission IN ({_sql_list(satisfying)})
                  AND ((da.grantee_type = 'user' AND da.grantee_id = CAST(:__access_user_id AS uuid))
                    OR (da.grantee_type = 'group' AND da.grantee_id = ANY(CAST(:__access_group_ids AS uuid[]))))
            )
        )
    )"""
    return f"{live} AND {access}" if live else access


def _access_params(current_user: CurrentUser) -> dict:
    """Params for _access_where_clause - fixed names, safe to merge into
    any query's params dict unconditionally (harmless if unreferenced,
    e.g. for an admin caller where the clause itself is never emitted)."""
    return {
        "__access_user_id": current_user.id,
        "__access_group_ids": current_user.group_ids or [],
        "__access_tenant_id": current_user.tenant_id,
    }


async def _document_visible(
    conn, table: str, doc_id: str, current_user: CurrentUser, permission: str = "read"
) -> bool:
    """Direct existence+permission check for one document - used wherever
    a single table+id is acted on outside the UNION list/queue queries
    (detail view, links, review actions). `permission` is the level the
    action needs (read to view, download to export, edit to change)."""
    access_clause = _access_where_clause(table, current_user, permission)
    where = "id = CAST(:id AS uuid)"
    if access_clause:
        where += f" AND {access_clause}"
    params = {"id": doc_id, **_access_params(current_user)}
    result = await conn.execute(text(f'SELECT 1 FROM "{table}" WHERE {where}'), params)
    return result.one_or_none() is not None


async def _require_document_permission(
    conn, table: str, doc_id: str, current_user: CurrentUser, permission: str
) -> None:
    """Raise unless current_user holds `permission` on the document: 404
    when they can't see it at all (its existence isn't revealed), 403 when
    they can see it but not at that level."""
    if await _document_visible(conn, table, doc_id, current_user, permission):
        return
    if permission != "read" and await _document_visible(conn, table, doc_id, current_user):
        raise HTTPException(
            status_code=403,
            detail=t(f"documents.permission_{permission}_required", current_user.locale),
        )
    raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))


async def _effective_permission(
    conn, table: str, doc_id: str, current_user: CurrentUser
) -> str | None:
    """Highest permission level current_user holds on one document
    (read/download/edit), or None when they can't see it at all - lets
    the UI show only the actions the backend will accept. One round trip:
    each level's clause is evaluated as a column over the same row."""
    levels = [
        (level, _access_where_clause(table, current_user, level) or "TRUE")
        for level in PERMISSION_LEVELS
    ]
    cols = ", ".join(f'({clause}) AS "{level}"' for level, clause in levels)
    result = await conn.execute(
        text(f'SELECT {cols} FROM "{table}" WHERE id = CAST(:id AS uuid)'),
        {"id": doc_id, **_access_params(current_user)},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    granted = [level for level in PERMISSION_LEVELS if row[level]]
    return granted[-1] if granted else None


def _upload_match(table: str, cols: dict[str, str]) -> str:
    """SQL condition: the row is the document the uploads :__bd_ids
    produced - filed from one of them, or re-extracted into by one (a new
    file version's upload names its document, see
    ingest_router._apply_reextraction). `table` comes from
    _get_tables_columns, i.e. a sanitized identifier, so it can be inlined."""
    assert re.fullmatch(r"[a-z0-9_]+", table), table
    conds = [
        "id IN (SELECT target_document_id FROM batch_documents"
        " WHERE id = ANY(CAST(:__bd_ids AS uuid[]))"
        f" AND target_table = '{table}')"
    ]
    if "batch_document_id" in cols:
        conds.insert(0, "batch_document_id = ANY(CAST(:__bd_ids AS uuid[]))")
    return "(" + " OR ".join(conds) + ")"


async def _filed_rows_permitted(
    conn,
    current_user: CurrentUser,
    tables: list[str],
    where,
    params: dict,
    permission: str,
) -> bool | None:
    """Evaluate `permission` on every row of `tables` matching `where` - a
    SQL condition, or a function of the table name returning one (None to
    skip that table) - with the same params for every table. None when no
    row matches at all, otherwise whether at least one matching row is
    permitted."""
    parts = []
    for table in tables:
        cond = where(table) if callable(where) else where
        if cond is None:
            continue
        clause = _access_where_clause(table, current_user, permission) or "TRUE"
        parts.append(f'SELECT ({clause}) AS ok FROM "{table}" WHERE {cond}')
    if not parts:
        return None
    rows = await conn.execute(
        text("\nUNION ALL\n".join(parts)), {**params, **_access_params(current_user)}
    )
    oks = [bool(r[0]) for r in rows]
    if not oks:
        return None
    return any(oks)


async def _filed_document_permitted(
    batch_document_ids: list[str], current_user: CurrentUser, permission: str
) -> bool:
    """Ingestion endpoints (pages, page actions) work on a batch_documents
    upload, not on a filed document. While the upload is still in the
    pipeline nothing is filed and the shared pipeline rules apply (any
    contributor of the tenant) - but once it has produced a document, the
    upload is that document's content and follows its access rules."""
    if not batch_document_ids:
        return True
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        permitted = await _filed_rows_permitted(
            conn,
            current_user,
            sorted(tables_cols),
            lambda table: _upload_match(table, tables_cols[table]),
            {"__bd_ids": batch_document_ids},
            permission,
        )
    return permitted is not False


def _doc_file_match(cols: dict[str, str]) -> str:
    """SQL condition: the row references one of :__paths as any of its
    files - only over the file columns this table actually has (tables
    from before multi-page/original tracking lack some)."""
    conds = [f"{c} = ANY(:__paths)" for c in ("source_image_path", "source_pdf_path", "original_path") if c in cols]
    if "page_image_paths" in cols:
        conds += [
            "page_image_paths @> jsonb_build_array(CAST(:__path0 AS text))",
            "page_image_paths @> jsonb_build_array(CAST(:__path1 AS text))",
        ]
    return "(" + " OR ".join(conds) + ")"


async def _named_document_has_file(
    conn,
    current_user: CurrentUser,
    tables_cols: dict[str, dict[str, str]],
    paths: list[str],
    table: str | None,
    doc_id: str | None,
) -> str | None:
    """The sanitized table name when the document the caller names (table
    + id) is readable by them and references one of `paths`, else None."""
    if not (table and doc_id):
        return None
    safe = _sanitize(table)
    if safe not in tables_cols:
        return None
    clause = _access_where_clause(safe, current_user)
    where = f"id = CAST(:__doc_id AS uuid) AND {_doc_file_match(tables_cols[safe])}"
    if clause:
        where += f" AND {clause}"
    row = await conn.execute(
        text(f'SELECT 1 FROM "{safe}" WHERE {where}'),
        {
            "__doc_id": doc_id,
            "__paths": paths,
            "__path0": paths[0],
            "__path1": paths[-1],
            **_access_params(current_user),
        },
    )
    return safe if row.one_or_none() is not None else None


async def _file_permitted(
    conn,
    current_user: CurrentUser,
    paths: list[str],
    table: str | None,
    doc_id: str | None,
) -> bool:
    """May current_user view this pipeline file (page image, original
    upload, source PDF)? `paths` is the path as requested and as resolved.

    1. Fast path - the caller names the document (table + id) it is
       showing: allowed when that document is readable and references the
       file.
    2. Otherwise the file is traced to the upload(s) that produced it
       (batch_documents / batch_document_pages, in the caller's tenant).
       If any of them has been filed, the filed document(s) decide; if none
       has, the file is still in the shared pipeline - contributors only.
    3. A file no upload knows about (documents that predate page
       tracking) is allowed only when a readable document references it.
    Anything else is refused - including another tenant's files."""
    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
    match_params = {"__paths": paths, "__path0": paths[0], "__path1": paths[-1]}

    if await _named_document_has_file(conn, current_user, tables_cols, paths, table, doc_id):
        return True

    bd_rows = await conn.execute(
        text("""
            SELECT bd.id::text FROM batch_documents bd
            JOIN batches b ON b.id = bd.batch_id
            WHERE b.tenant_id = CAST(:tid AS uuid)
              AND (bd.image_path = ANY(:paths) OR bd.source_path = ANY(:paths))
            UNION
            SELECT bd.id::text FROM batch_document_pages p
            JOIN batch_documents bd ON bd.id = p.batch_document_id
            JOIN batches b ON b.id = bd.batch_id
            WHERE b.tenant_id = CAST(:tid AS uuid) AND p.image_path = ANY(:paths)
        """),
        {"tid": current_user.tenant_id, "paths": paths},
    )
    bd_ids = [r[0] for r in bd_rows]

    if bd_ids:
        permitted = await _filed_rows_permitted(
            conn,
            current_user,
            sorted(tables_cols),
            lambda table: _upload_match(table, tables_cols[table]),
            {"__bd_ids": bd_ids},
            "read",
        )
        if permitted is None:
            return current_user.role in ("admin", "reviewer")
        return permitted

    permitted = await _filed_rows_permitted(
        conn,
        current_user,
        sorted(tables_cols),
        lambda table: _doc_file_match(tables_cols[table]),
        match_params,
        "read",
    )
    return bool(permitted)


def _per_table_select(
    table: str,
    cols: dict[str, str],
    *,
    q: str | None,
    confidence: str | None,
    review_status: str | None,
    date_from: str | None,
    date_to: str | None,
    current_user: CurrentUser,
    reviewed_by: str | None = None,
    series: str | None = None,
) -> str | None:
    """Build the per-table SELECT fragment for the UNION.  Returns None when
    the table should be excluded (e.g. q is set but table has no text cols,
    or reviewed_by is set but the table has never had a reviewed_by column
    added - see patch_review's lazy-ALTER - so it can't have any matches).

    Every branch projects the same fixed shape (id, table_name,
    document_type, confidence, review_status, ingested_at,
    source_image_path, extra_fields) regardless of the table's actual
    schema - extra_fields is a JSONB blob of whatever non-system columns
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
        if extra
        else "'{}'::jsonb"
    )

    sel = [
        "id::text AS id",
        f"'{table}'::text AS table_name",
        (
            "document_type::text AS document_type"
            if "document_type" in cols
            else "NULL::text AS document_type"
        ),
        "record_id::text AS record_id" if "record_id" in cols else "NULL::text AS record_id",
        "confidence::text    AS confidence",
        "review_status::text AS review_status",
        "ingested_at::text   AS ingested_at",
        "source_image_path",
        (
            "reviewed_by::text AS reviewed_by"
            if "reviewed_by" in cols
            else "NULL::text AS reviewed_by"
        ),
        "series_id::text AS series_id" if "series_id" in cols else "NULL::text AS series_id",
        f"{extra_expr} AS extra_fields",
    ]

    where: list[str] = []
    if q:
        # Use the GIN index created by _ensure_table for fast FTS on large tables.
        # The expression must match the index expression exactly so PostgreSQL picks it up.
        coalesces = " || ' ' || ".join(f"COALESCE({c},'')" for c in searchable)
        where.append(f"to_tsvector('french', {coalesces})" f" @@ plainto_tsquery('french', :q)")
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
    only_table: str | None,
    q: str | None,
    confidence: str | None,
    review_status: str | None,
    date_from: str | None,
    date_to: str | None,
    page: int,
    page_size: int,
    current_user: CurrentUser,
    reviewed_by: str | None = None,
    series: str | None = None,
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
            tbl,
            cols,
            q=q,
            confidence=confidence,
            review_status=review_status,
            date_from=date_from,
            date_to=date_to,
            reviewed_by=reviewed_by,
            series=series,
            current_user=current_user,
        )
        for tbl, cols in scope.items()
    ]
    parts = [p for p in parts if p]
    if not parts:
        return [], 0

    union_sql = "\nUNION ALL\n".join(parts)
    params: dict = {
        "page_size": page_size,
        "offset": (page - 1) * page_size,
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
    rows = result.mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0
    return [{k: v for k, v in dict(r).items() if k != "total_count"} for r in rows], total


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/db", tags=["documents-db"])


@router.get("/types", summary="List document types")
@cache(expire=60, namespace="types", key_builder=_path_key_builder)
async def list_types(current_user: CurrentUser = Depends(require_user)):
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
            # guaranteed base one - tables populated only by crashed
            # extractions (empty fields) never get it created.
            doc_type_expr = (
                "MAX(document_type)" if "document_type" in tables_cols[table] else "NULL"
            )
            access_clause = _access_where_clause(table, current_user)
            where_str = f"WHERE {access_clause}" if access_clause else ""
            row = await conn.execute(
                text(
                    f"SELECT COUNT(*), MAX(ingested_at), "
                    f'{doc_type_expr} FROM "{table}" {where_str}'
                ),
                _access_params(current_user) if access_clause else {},
            )
            count, last_ingested, doc_type = row.one()
            results.append(
                {
                    "table_name": table,
                    "document_type": doc_type or table,
                    "count": int(count),
                    "last_ingested": (last_ingested.isoformat() if last_ingested else None),
                }
            )
        return results


@router.get("/reviewers", summary="List reviewers")
@cache(expire=60, namespace="reviewers", key_builder=_path_key_builder)
async def list_reviewers(current_user: CurrentUser = Depends(require_user)):
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
        # from the caller doesn't show up here either - even their bare
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
        results = [{"id": str(r[0]), "email": r[1], "full_name": r[2]} for r in user_rows]
        results.sort(key=lambda r: (r["full_name"] or r["email"]).lower())
        return results


@router.get("/documents")
@limiter.limit("120/minute")
async def list_documents(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    document_type: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    reviewed_by: str | None = None,
    series: str | None = None,
    current_user: CurrentUser = Depends(require_user),
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
            conn,
            tables_cols,
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
    """JSON-safe dict from a full-row Mapping - dates as ISO strings,
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
            data[k] = v  # JSONB - let FastAPI serialize as proper JSON
        else:
            data[k] = str(v)
    return data


# One document_viewed / original-file document_downloaded audit entry per
# user and document (file) within this many seconds - opening a document
# reloads its detail and files several times.
ACCESS_AUDIT_WINDOW_SECONDS = 300


@router.get("/documents/{table_name}/{doc_id}", summary="Document detail")
async def get_document_detail(
    request: Request,
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
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
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )

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
        raise HTTPException(
            status_code=404, detail=t("common.document_not_found", current_user.locale)
        )

    if await first_in_window(
        f"viewed:{current_user.id}:{safe}:{doc_id}", ACCESS_AUDIT_WINDOW_SECONDS
    ):
        await log_action(
            action="document_viewed",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=safe,
            document_id=doc_id,
            ip_address=client_ip(request),
        )
    return _serialize_row(row)


@router.get("/documents/{table_name}/{doc_id}/export", summary="Export one document")
@limiter.limit("30/minute")
async def export_document(
    request: Request,
    table_name: str,
    doc_id: str,
    format: str = Query("json", pattern="^(json|sql|csv)$"),
    field: str | None = Query(None, max_length=128),
    current_user: CurrentUser = Depends(require_user),
):
    """Download one document's data - the per-document counterpart of the
    admin archive export (GET /api/admin/export), same JSON and SQL shapes,
    data only (no source files). Anyone who can view the document can
    export it.

    - json: {"table", "exported_at", "document": {...every column}}
    - sql:  CREATE TABLE IF NOT EXISTS + one INSERT, replayable elsewhere
    - csv:  one list field (?field=entries) as a spreadsheet, one row per
            item and one column per item key
    """
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        await _require_document_permission(conn, safe, doc_id, current_user, "download")
        result = await conn.execute(
            text(f'SELECT * FROM "{safe}" WHERE id = CAST(:id AS uuid)'), {"id": doc_id}
        )
        row = result.mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=t("common.document_not_found", current_user.locale))
    row = dict(row)
    exported_at = datetime.now(timezone.utc)
    stem = f"{safe}_{str(row.get('id'))[:8]}"

    if format == "json":
        body = json.dumps(
            {
                "table": safe,
                "exported_at": exported_at.isoformat(),
                "document": {k: serial(v) for k, v in row.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        filename, media_type = f"{stem}.json", "application/json"
    elif format == "sql":
        col_defs = ", ".join(f'"{c}" {ty}' for c, ty in tables_cols[safe].items())
        body = "\n".join(
            [
                f"-- SDAI document export - {safe} {row.get('id')} - {exported_at.isoformat()}",
                f'CREATE TABLE IF NOT EXISTS "{safe}" ({col_defs});',
                insert_statement(safe, row),
                "",
            ]
        )
        filename, media_type = f"{stem}.sql", "application/sql"
    else:
        if not field:
            raise HTTPException(status_code=422, detail=t("documents.export_field_required", current_user.locale))
        items = row.get(field)
        if not isinstance(items, list):
            raise HTTPException(
                status_code=422, detail=t("documents.export_field_not_list", current_user.locale, field=field)
            )
        body = list_to_csv(items)
        filename, media_type = f"{stem}_{_sanitize(field)}.csv", "text/csv; charset=utf-8"

    await log_action(
        action="document_exported",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"format": format, **({"field": field} if format == "csv" else {})},
        ip_address=client_ip(request),
    )
    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class UpdateDocumentFieldsBody(BaseModel):
    fields: dict


@router.patch("/documents/{table_name}/{doc_id}/fields", summary="Edit extraction field data")
async def update_document_fields(
    table_name: str,
    doc_id: str,
    body: UpdateDocumentFieldsBody,
    current_user: CurrentUser = Depends(require_extraction_editor),
):
    """Correct extracted field values on a document regardless of its
    review status - including one that's already been approved and filed.
    Requires the extraction-editing permission (admin, or a reviewer
    delegated can_edit_extraction). Distinct from patch_review's approve
    action: this never touches review_status/confidence/reviewed_by -
    it's a pure content correction, not a review-workflow transition, and
    it's not available anywhere in the read-only document browser/detail
    pages for anyone without that permission."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")

        cols = tables_cols[safe]
        editable, set_parts, params = _build_field_set_clause(cols, body.fields)
        if not editable:
            raise HTTPException(
                status_code=422, detail=t("documents.no_editable_fields", current_user.locale)
            )

        old = await _row_for_update(conn, safe, doc_id)
        params["id"] = doc_id
        result = await conn.execute(
            text(
                f'UPDATE "{safe}" SET {", ".join(set_parts)} WHERE id = CAST(:id AS uuid) RETURNING *'
            ),
            params,
        )
        row = result.mappings().one_or_none()
        changes = await _record_edit_history(
            conn, current_user, safe, doc_id, old, row, editable.keys(), "edit"
        )

    if row is None:
        raise HTTPException(
            status_code=404, detail=t("common.document_not_found", current_user.locale)
        )

    await log_action(
        action="document_fields_edited",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={"fields_changed": list(editable.keys()), "changes": changes},
    )

    return _serialize_row(row)


# ── Document links (chain-of-custody) ─────────────────────────────────────────


class CreateLinkBody(BaseModel):
    to_table: str
    to_id: str
    relation: str = Field(..., min_length=1, max_length=100)
    note: str | None = None


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


async def _enrich_links(
    conn, current_user: CurrentUser, table_name: str, doc_id: str
) -> list[dict]:
    """Fetch every link involving (table_name, doc_id) and attach enough
    info about the *other* document (type, a display value, image path)
    for the frontend to render something useful without a second
    round-trip per link. Batched per distinct other-table, not per-link.
    An "other" document current_user isn't allowed to see is simply
    excluded from the batch fetch below, which the existing
    other_docs.get(...) is None fallback already turns into
    other_missing: true - no separate redaction logic needed."""
    rows = (
        (
            await conn.execute(
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
            )
        )
        .mappings()
        .all()
    )

    if not rows:
        return []

    tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)

    # (other_table -> {other_id -> row}) fetched once per distinct table.
    other_ids_by_table: dict[str, set[str]] = {}
    for r in rows:
        outgoing = r["from_table"] == table_name and str(r["from_id"]) == doc_id
        other_table = r["to_table"] if outgoing else r["from_table"]
        other_id = str(r["to_id"] if outgoing else r["from_id"])
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
        other_rows = (
            (
                await conn.execute(
                    text(
                        f"SELECT id::text AS id, document_type, source_image_path,"
                        f" {display_expr} AS display"
                        f' FROM "{other_table}" WHERE {where}'
                    ),
                    {"ids": list(ids), **_access_params(current_user)},
                )
            )
            .mappings()
            .all()
        )
        for r in other_rows:
            other_docs[(other_table, r["id"])] = dict(r)

    links = []
    for r in rows:
        outgoing = r["from_table"] == table_name and str(r["from_id"]) == doc_id
        other_table = r["to_table"] if outgoing else r["from_table"]
        other_id = str(r["to_id"] if outgoing else r["from_id"])
        other = other_docs.get((other_table, other_id))
        # A linked document the caller can't see stays an anonymous
        # placeholder: no title, no type, no id, no note (a note is free
        # text about it) - only that some link exists and its relation.
        links.append(
            {
                "id": str(r["id"]),
                "relation": r["relation"],
                "note": r["note"] if other else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "created_by": str(r["created_by"]) if r["created_by"] else None,
                "direction": "outgoing" if outgoing else "incoming",
                "other_table": other_table if other else "",
                "other_id": other_id if other else "",
                "other_document_type": other["document_type"] if other else None,
                "other_display": other["display"] if other else None,
                "other_source_image_path": other["source_image_path"] if other else None,
                "other_missing": other is None,
            }
        )
    return links


@router.get("/documents/{table_name}/{doc_id}/links", summary="Document links")
async def get_document_links(
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Return every chain-of-custody link involving this document, in
    either direction, enriched with basic info about the linked document."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(
                status_code=404, detail=t("common.document_not_found", current_user.locale)
            )

        links = await _enrich_links(conn, current_user, safe, doc_id)

    return {"links": links}


@router.post("/documents/{table_name}/{doc_id}/links", summary="Link to another document")
@limiter.limit("60/minute")
async def create_document_link(
    request: Request,
    table_name: str,
    doc_id: str,
    body: CreateLinkBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Create a chain-of-custody link from this document to another (e.g.
    a sale deed 'concerns' the original title deed). Both documents must
    belong to the caller's own tenant - this check IS the tenant-isolation
    boundary for this feature, since a real FK against a dynamically-named
    table isn't possible. Idempotent: an identical (from, to, relation)
    link already existing returns the existing row rather than erroring."""
    safe = _sanitize(table_name)
    to_safe = _sanitize(body.to_table)

    if safe == to_safe and doc_id == body.to_id:
        raise HTTPException(
            status_code=400, detail=t("documents.cannot_link_self", current_user.locale)
        )

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        if to_safe not in tables_cols:
            raise HTTPException(
                status_code=404,
                detail=t("common.table_not_found", current_user.locale, table=to_safe),
            )

        await _require_document_permission(conn, safe, doc_id, current_user, "edit")
        if not await _document_visible(conn, to_safe, body.to_id, current_user):
            raise HTTPException(
                status_code=404,
                detail=t("documents.target_document_not_found", current_user.locale),
            )

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
                "tid": current_user.tenant_id,
                "ft": safe,
                "fid": doc_id,
                "tt": to_safe,
                "tidoc": body.to_id,
                "rel": body.relation,
                "note": body.note,
                "cb": current_user.id,
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
                    "tid": current_user.tenant_id,
                    "ft": safe,
                    "fid": doc_id,
                    "tt": to_safe,
                    "tidoc": body.to_id,
                    "rel": body.relation,
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
    request: Request,
    link_id: str,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Remove a chain-of-custody link. Any tenant user may do this (not
    admin-only) - removing a link only retracts an assertion between two
    still-intact documents, no extracted data is destroyed, and it's
    trivially re-creatable; this matches flag/approve/reject's permission
    level, not hard-delete's. Requires the caller to hold edit permission
    on at least one side of the link - this endpoint only has a link_id,
    not a table+id the way create/list do, so there's no single "the
    document you're viewing" to check against."""
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
            raise HTTPException(
                status_code=404, detail=t("common.link_not_found", current_user.locale)
            )

        can_edit_from = await _document_visible(
            conn, link["from_table"], str(link["from_id"]), current_user, "edit"
        )
        can_edit_to = await _document_visible(
            conn, link["to_table"], str(link["to_id"]), current_user, "edit"
        )
        if not (can_edit_from or can_edit_to):
            raise HTTPException(
                status_code=404, detail=t("common.link_not_found", current_user.locale)
            )

        result = await conn.execute(
            text(
                "DELETE FROM sdai_document_links WHERE id = CAST(:id AS uuid) RETURNING from_table, from_id, to_table, to_id, relation"
            ),
            {"id": link_id},
        )
        row = result.mappings().one_or_none()

    await log_action(
        action="document_link_removed",
        user_id=current_user.id,
        user_email=current_user.email,
        details={
            "from_table": row["from_table"],
            "from_id": str(row["from_id"]),
            "to_table": row["to_table"],
            "to_id": str(row["to_id"]),
            "relation": row["relation"],
        },
        ip_address=client_ip(request),
    )

    return {"ok": True}


# ── Document access grants ─────────────────────────────────────────────────────


@router.get("/grantees", summary="Groups and users available to tag a document with")
async def list_grantees(current_user: CurrentUser = Depends(require_access_manager)):
    """Minimal {id, name}/{id, email, full_name} listing for the access-grant
    picker - deliberately not the same data as GET /api/admin/groups or
    GET /api/admin/users (roles, can_manage_access flags, member counts),
    which stay admin-only. A can_manage_access reviewer can tag a document
    without needing that broader admin view, so this is scoped to exactly
    what the picker needs and gated by require_access_manager instead."""
    async with _engine().connect() as conn:
        group_rows = await conn.execute(
            text(
                "SELECT id, name FROM sdai_groups WHERE tenant_id = CAST(:tid AS uuid) ORDER BY name"
            ),
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
    grantee_id: str
    permission: str = Field("read", pattern="^(read|download|edit)$")


class CreateScopedGrantBody(CreateAccessGrantBody):
    scope: str = Field(..., pattern="^(series|doc_type)$")
    series_id: str | None = None
    table_name: str | None = None


class UpdateGrantBody(BaseModel):
    permission: str = Field(..., pattern="^(read|download|edit)$")


_GRANT_COLS = (
    "id, scope, table_name, document_id, series_id, grantee_type, grantee_id,"
    " permission, granted_by, granted_at"
)


async def _serialize_grants(conn, rows) -> list[dict]:
    """Grant rows -> JSON, with grantee (group/user) and series names
    resolved in one query per kind rather than per grant."""
    group_ids = [str(r["grantee_id"]) for r in rows if r["grantee_type"] == "group"]
    user_ids = [str(r["grantee_id"]) for r in rows if r["grantee_type"] == "user"]
    series_ids = [str(r["series_id"]) for r in rows if r["series_id"]]
    group_names: dict[str, str] = {}
    user_names: dict[str, str] = {}
    series_names: dict[str, str] = {}
    if group_ids:
        g_rows = await conn.execute(
            text("SELECT id, name FROM sdai_groups WHERE id = ANY(:ids)"), {"ids": group_ids}
        )
        group_names = {str(r[0]): r[1] for r in g_rows}
    if user_ids:
        u_rows = await conn.execute(
            text("SELECT id, email, full_name FROM sdai_users WHERE id = ANY(:ids)"),
            {"ids": user_ids},
        )
        user_names = {str(r[0]): (r[2] or r[1]) for r in u_rows}
    if series_ids:
        s_rows = await conn.execute(
            text("SELECT id, name FROM sdai_series WHERE id = ANY(:ids)"), {"ids": series_ids}
        )
        series_names = {str(r[0]): r[1] for r in s_rows}

    grants = []
    for r in rows:
        gid = str(r["grantee_id"])
        name = group_names.get(gid) if r["grantee_type"] == "group" else user_names.get(gid)
        series_id = str(r["series_id"]) if r["series_id"] else None
        grants.append(
            {
                "id": str(r["id"]),
                "scope": r["scope"],
                "table_name": r["table_name"],
                "series_id": series_id,
                "series_name": series_names.get(series_id) if series_id else None,
                "grantee_type": r["grantee_type"],
                "grantee_id": gid,
                "grantee_name": name or "(removed)",
                "permission": r["permission"],
                "granted_by": str(r["granted_by"]) if r["granted_by"] else None,
                "granted_at": r["granted_at"].isoformat() if r["granted_at"] else None,
            }
        )
    return grants


async def _check_grantee(conn, current_user: CurrentUser, grantee_type: str, grantee_id: str) -> None:
    """404 unless the group/user exists in the caller's own tenant."""
    table = "sdai_groups" if grantee_type == "group" else "sdai_users"
    row = await conn.execute(
        text(
            f"SELECT 1 FROM {table} WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
        ),
        {"id": grantee_id, "tid": current_user.tenant_id},
    )
    if row.one_or_none() is None:
        raise HTTPException(
            status_code=404,
            detail=t(
                "common.group_not_found" if grantee_type == "group" else "common.user_not_found",
                current_user.locale,
            ),
        )


@router.get("/documents/{table_name}/{doc_id}/access", summary="List access grants")
async def get_document_access(
    table_name: str,
    doc_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Every grant that restricts this document - its own (scope
    'document') and those it inherits from its series or its document type
    - plus the caller's own effective permission on it, so the UI can hide
    actions the backend would refuse. Visible read-only to anyone who can
    already see the document - editing is gated separately, see POST/DELETE
    below. An empty list means the document is open to the whole tenant
    (within each user's clearance)."""
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        my_permission = await _effective_permission(conn, safe, doc_id, current_user)
        if my_permission is None:
            raise HTTPException(
                status_code=404, detail=t("common.document_not_found", current_user.locale)
            )

        rows = (
            (
                await conn.execute(
                    text(f"""
                SELECT {_GRANT_COLS}
                FROM sdai_document_access
                WHERE tenant_id = CAST(:tid AS uuid)
                  AND ((scope = 'document' AND table_name = :t AND document_id = CAST(:id AS uuid))
                    OR (scope = 'series'
                        AND series_id = (SELECT series_id FROM "{safe}" WHERE id = CAST(:id AS uuid)))
                    OR (scope = 'doc_type' AND table_name = :t))
                ORDER BY CASE scope WHEN 'document' THEN 0 WHEN 'series' THEN 1 ELSE 2 END,
                         granted_at
            """),
                    {"tid": current_user.tenant_id, "t": safe, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
        grants = await _serialize_grants(conn, rows)

    return {
        "grants": grants,
        "can_manage": current_user.can_manage_document_access,
        "my_permission": my_permission,
    }


@router.post("/documents/{table_name}/{doc_id}/access", summary="Grant access to a document")
async def create_document_access(
    table_name: str,
    doc_id: str,
    body: CreateAccessGrantBody,
    current_user: CurrentUser = Depends(require_access_manager),
):
    """Restrict a document by granting a group or a specific person
    access to it at a permission level (read/download/edit). The first
    grant on a previously-open document is what makes it restricted at all
    - see sdai_document_access's docstring in ingest_router._INIT_DDL.
    Granting an existing grantee again changes its level."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        # can_manage_access still can't grant access to a document they
        # can't already see (an admin bypasses this, same as everywhere else).
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(
                status_code=404, detail=t("common.document_not_found", current_user.locale)
            )
        await _check_grantee(conn, current_user, body.grantee_type, body.grantee_id)

        result = await conn.execute(
            text("""
                INSERT INTO sdai_document_access
                    (tenant_id, table_name, document_id, grantee_type, grantee_id, granted_by,
                     scope, permission)
                VALUES (CAST(:tid AS uuid), :t, CAST(:id AS uuid), :gt, CAST(:gid AS uuid),
                        CAST(:by AS uuid), 'document', :perm)
                ON CONFLICT (tenant_id, table_name, document_id, grantee_type, grantee_id)
                    WHERE scope = 'document'
                    DO UPDATE SET permission = EXCLUDED.permission
                RETURNING id
            """),
            {
                "tid": current_user.tenant_id,
                "t": safe,
                "id": doc_id,
                "gt": body.grantee_type,
                "gid": body.grantee_id,
                "by": current_user.id,
                "perm": body.permission,
            },
        )
        grant_id = result.scalar()

    await log_action(
        action="document_access_granted",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=safe,
        document_id=doc_id,
        details={
            "scope": "document",
            "grantee_type": body.grantee_type,
            "grantee_id": body.grantee_id,
            "permission": body.permission,
        },
    )
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"id": str(grant_id)}


@router.get("/grants", summary="List series-level or type-level grants")
async def list_scoped_grants(
    scope: str = Query(..., pattern="^(series|doc_type)$"),
    current_user: CurrentUser = Depends(require_admin),
):
    """Every grant of one scope in this tenant - powers the series and
    document-type grant editors on the access admin page."""
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                SELECT {_GRANT_COLS} FROM sdai_document_access
                WHERE tenant_id = CAST(:tid AS uuid) AND scope = :scope
                ORDER BY granted_at
            """),
                    {"tid": current_user.tenant_id, "scope": scope},
                )
            )
            .mappings()
            .all()
        )
        return {"grants": await _serialize_grants(conn, rows)}


@router.post("/grants", summary="Grant access to a whole series or document type")
async def create_scoped_grant(
    body: CreateScopedGrantBody,
    current_user: CurrentUser = Depends(require_admin),
):
    """Restrict every document of a series (scope 'series', series_id) or
    of a document type (scope 'doc_type', table_name) to the grantee - and
    to whoever else holds a grant on them. Admin-only: a single grant here
    can close off a whole part of the archive. Granting an existing
    grantee again changes its level."""
    async with _engine().begin() as conn:
        if body.scope == "series":
            if not body.series_id:
                raise HTTPException(
                    status_code=422, detail=t("documents.grant_target_required", current_user.locale)
                )
            row = await conn.execute(
                text(
                    "SELECT 1 FROM sdai_series WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"id": body.series_id, "tid": current_user.tenant_id},
            )
            if row.one_or_none() is None:
                raise HTTPException(
                    status_code=404, detail=t("common.series_not_found", current_user.locale)
                )
            target_table, target_series = None, body.series_id
            conflict = "(tenant_id, series_id, grantee_type, grantee_id) WHERE scope = 'series'"
        else:
            if not body.table_name:
                raise HTTPException(
                    status_code=422, detail=t("documents.grant_target_required", current_user.locale)
                )
            safe = _sanitize(body.table_name)
            tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
            if safe not in tables_cols:
                raise HTTPException(
                    status_code=404,
                    detail=t("common.table_not_found", current_user.locale, table=safe),
                )
            target_table, target_series = safe, None
            conflict = "(tenant_id, table_name, grantee_type, grantee_id) WHERE scope = 'doc_type'"

        await _check_grantee(conn, current_user, body.grantee_type, body.grantee_id)

        result = await conn.execute(
            text(f"""
                INSERT INTO sdai_document_access
                    (tenant_id, scope, table_name, series_id, grantee_type, grantee_id,
                     granted_by, permission)
                VALUES (CAST(:tid AS uuid), :scope, :t, CAST(:sid AS uuid), :gt,
                        CAST(:gid AS uuid), CAST(:by AS uuid), :perm)
                ON CONFLICT {conflict} DO UPDATE SET permission = EXCLUDED.permission
                RETURNING id
            """),
            {
                "tid": current_user.tenant_id,
                "scope": body.scope,
                "t": target_table,
                "sid": target_series,
                "gt": body.grantee_type,
                "gid": body.grantee_id,
                "by": current_user.id,
                "perm": body.permission,
            },
        )
        grant_id = result.scalar()

    await log_action(
        action="document_access_granted",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=target_table,
        details={
            "scope": body.scope,
            "series_id": target_series,
            "grantee_type": body.grantee_type,
            "grantee_id": body.grantee_id,
            "permission": body.permission,
        },
    )
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"id": str(grant_id)}


async def _load_grant_for_change(conn, grant_id: str, current_user: CurrentUser):
    """The grant row, if it belongs to the caller's tenant and the caller
    may manage it: document grants need the access-management permission
    and visibility of the document, series/type grants need an admin."""
    row = (
        (
            await conn.execute(
                text(f"""
            SELECT {_GRANT_COLS} FROM sdai_document_access
            WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
                {"id": grant_id, "tid": current_user.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=t("common.grant_not_found", current_user.locale))
    if row["scope"] != "document":
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail=t("auth.admin_required", current_user.locale))
    elif current_user.role != "admin" and not await _document_visible(
        conn, row["table_name"], str(row["document_id"]), current_user
    ):
        raise HTTPException(status_code=404, detail=t("common.grant_not_found", current_user.locale))
    return row


@router.patch("/access/{grant_id}", summary="Change an access grant's permission level")
async def update_document_access(
    grant_id: str,
    body: UpdateGrantBody,
    current_user: CurrentUser = Depends(require_access_manager),
):
    async with _engine().begin() as conn:
        row = await _load_grant_for_change(conn, grant_id, current_user)
        await conn.execute(
            text("UPDATE sdai_document_access SET permission = :perm WHERE id = CAST(:id AS uuid)"),
            {"perm": body.permission, "id": grant_id},
        )

    await log_action(
        action="document_access_changed",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=row["table_name"],
        document_id=str(row["document_id"]) if row["document_id"] else None,
        details={
            "scope": row["scope"],
            "series_id": str(row["series_id"]) if row["series_id"] else None,
            "grantee_type": row["grantee_type"],
            "grantee_id": str(row["grantee_id"]),
            "from": row["permission"],
            "to": body.permission,
        },
    )
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"ok": True}


@router.delete("/access/{grant_id}", summary="Remove an access grant")
async def delete_document_access(
    grant_id: str,
    current_user: CurrentUser = Depends(require_access_manager),
):
    """Remove a grant of any scope. Removing the last grant that targets a
    document opens it to the whole tenant again."""
    async with _engine().begin() as conn:
        row = await _load_grant_for_change(conn, grant_id, current_user)
        await conn.execute(
            text("DELETE FROM sdai_document_access WHERE id = CAST(:id AS uuid)"), {"id": grant_id}
        )

    await log_action(
        action="document_access_revoked",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=row["table_name"],
        document_id=str(row["document_id"]) if row["document_id"] else None,
        details={
            "scope": row["scope"],
            "series_id": str(row["series_id"]) if row["series_id"] else None,
            "grantee_type": row["grantee_type"],
            "grantee_id": str(row["grantee_id"]),
        },
    )
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"ok": True}


class SetConfidentialityBody(BaseModel):
    confidentiality: str = Field(..., pattern="^(public|internal|confidential|secret)$")


@router.patch(
    "/documents/{table_name}/{doc_id}/confidentiality", summary="Set a document's confidentiality"
)
async def set_document_confidentiality(
    request: Request,
    table_name: str,
    doc_id: str,
    body: SetConfidentialityBody,
    current_user: CurrentUser = Depends(require_access_manager),
):
    """Classify a document public / internal / confidential / secret. Only
    users whose clearance reaches a level can see documents at that level
    (admins see all) - so, like a grant, this is access management:
    admin or can_manage_access. A non-admin can't classify above their own
    clearance, which would hide the document from themselves."""
    safe = _sanitize(table_name)
    level = body.confidentiality
    if current_user.role != "admin" and CONFIDENTIALITY_LEVELS.index(
        level
    ) > CONFIDENTIALITY_LEVELS.index(
        current_user.clearance if current_user.clearance in CONFIDENTIALITY_LEVELS else "public"
    ):
        raise HTTPException(
            status_code=403, detail=t("documents.confidentiality_above_clearance", current_user.locale)
        )

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        if not await _document_visible(conn, safe, doc_id, current_user):
            raise HTTPException(
                status_code=404, detail=t("common.document_not_found", current_user.locale)
            )
        previous = (
            await conn.execute(
                text(f'SELECT confidentiality FROM "{safe}" WHERE id = CAST(:id AS uuid) FOR UPDATE'),
                {"id": doc_id},
            )
        ).scalar()
        await conn.execute(
            text(f'UPDATE "{safe}" SET confidentiality = :level WHERE id = CAST(:id AS uuid)'),
            {"level": level, "id": doc_id},
        )

    if previous != level:
        await log_action(
            action="confidentiality_changed",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=safe,
            document_id=doc_id,
            details={"from": previous, "to": level},
            ip_address=client_ip(request),
        )
        await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"ok": True, "confidentiality": level}


# ── Fonds/series hierarchy ──────────────────────────────────────────────────
# One document belongs to at most one series (the standard archival model,
# not many-to-many). Series creation is admin-only (structural, like
# groups); assigning an existing series to a document is open to any
# tenant user - organizational, not a security control.


class CreateSeriesBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None


class AssignSeriesBody(BaseModel):
    series_id: str | None = None


@router.get("/series", summary="List series")
async def list_series(current_user: CurrentUser = Depends(require_user)):
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
                "id": str(r[0]),
                "name": r[1],
                "description": r[2],
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
                "tid": current_user.tenant_id,
                "name": body.name,
                "description": body.description,
                "cb": current_user.id,
            },
        )
        series_id = result.scalar()
        if series_id is None:
            raise HTTPException(
                status_code=409,
                detail=t("common.series_already_exists", current_user.locale, name=body.name),
            )

    await log_action(
        action="series_created",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"series_id": str(series_id), "name": body.name},
    )
    return {"id": str(series_id), "name": body.name}


@router.delete("/series/{series_id}", summary="Delete a series")
async def delete_series(series_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Documents keep their series_id pointing at a now-nonexistent row -
    no FK from the dynamic tables to sdai_series (there are too many of
    them, per document type, to maintain one) - so the frontend treats an
    unresolvable series_id the same as "no series", matching how a removed
    access grant's grantee is already handled."""
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(
                "DELETE FROM sdai_series WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid) RETURNING id"
            ),
            {"id": series_id, "tid": current_user.tenant_id},
        )
        if result.one_or_none() is None:
            raise HTTPException(
                status_code=404, detail=t("common.series_not_found", current_user.locale)
            )
    return {"ok": True}


@router.patch("/documents/{table_name}/{doc_id}/series", summary="Assign a document to a series")
async def assign_document_series(
    table_name: str,
    doc_id: str,
    body: AssignSeriesBody,
    current_user: CurrentUser = Depends(require_reviewer),
):
    """Open to any reviewer holding edit permission on the document -
    organizational filing, not access management (unlike access grants,
    gated behind require_access_manager). Moving a document between series
    can change who sees it when either series carries grants."""
    safe = _sanitize(table_name)

    async with _engine().begin() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if safe not in tables_cols:
            raise HTTPException(
                status_code=404, detail=t("common.table_not_found", current_user.locale, table=safe)
            )
        await _require_document_permission(conn, safe, doc_id, current_user, "edit")

        if body.series_id is not None:
            series_row = await conn.execute(
                text(
                    "SELECT 1 FROM sdai_series WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)"
                ),
                {"id": body.series_id, "tid": current_user.tenant_id},
            )
            if series_row.one_or_none() is None:
                raise HTTPException(
                    status_code=404, detail=t("common.series_not_found", current_user.locale)
                )

        await conn.execute(
            text(
                f'UPDATE "{safe}" SET series_id = CAST(:sid AS uuid) WHERE id = CAST(:id AS uuid)'
            ),
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
    await invalidate_cache(*_ACCESS_CACHE_NAMESPACES)
    return {"ok": True}


@router.get("/schema")
@limiter.limit("30/minute")
@cache(expire=30, namespace="schema", key_builder=_path_key_builder)
async def get_schema(request: Request, current_user: CurrentUser = Depends(require_user)):
    """
    Return full schema: tables, columns (with type/nullable), row counts,
    last ingested, and foreign key relationships.
    Everything discovered from information_schema - no table names hardcoded.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
        if not tables_cols:
            return {"tables": []}

        # Fetch column details for all ingest tables in one query
        col_result = await conn.execute(
            text("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """),
            {"tables": list(tables_cols.keys())},
        )

        table_columns: dict[str, list[dict]] = {}
        for row in col_result:
            table_columns.setdefault(row[0], []).append(
                {
                    "name": row[1],
                    "type": row[2],
                    "nullable": row[3] == "YES",
                }
            )

        # Fetch foreign keys for all ingest tables in one query
        fk_result = await conn.execute(
            text("""
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
        """),
            {"tables": list(tables_cols.keys())},
        )

        table_fks: dict[str, list[dict]] = {}
        for row in fk_result:
            table_fks.setdefault(row[0], []).append(
                {
                    "column": row[1],
                    "references_table": row[2],
                    "references_column": row[3],
                }
            )

        results = []
        for table_name in sorted(tables_cols):
            # document_type is a dynamic VLM-derived column, not a
            # guaranteed base one - tables populated only by crashed
            # extractions (empty fields) never get it created.
            has_doc_type = any(
                c["name"] == "document_type" for c in table_columns.get(table_name, [])
            )
            doc_type_expr = "MAX(document_type)" if has_doc_type else "NULL"
            access_clause = _access_where_clause(table_name, current_user)
            where_str = f"WHERE {access_clause}" if access_clause else ""
            stat = await conn.execute(
                text(
                    f'SELECT COUNT(*), MAX(ingested_at), {doc_type_expr} FROM "{table_name}" {where_str}'
                ),
                _access_params(current_user) if access_clause else {},
            )
            count, last_ingested, doc_type = stat.one()
            results.append(
                {
                    "name": table_name,
                    "document_type": doc_type or table_name,
                    "row_count": int(count),
                    "last_ingested": last_ingested.isoformat() if last_ingested else None,
                    "columns": table_columns.get(table_name, []),
                    "foreign_keys": table_fks.get(table_name, []),
                }
            )

    return {"tables": results}


@router.get("/image")
async def serve_processed_image(
    request: Request,
    path: str,
    table: str | None = None,
    id: str | None = None,
    current_user: CurrentUser = Depends(require_user),
):
    """
    Serve a document-pipeline file by absolute path - a processed page
    image, an original uploaded PDF, or a source file ingested via a
    server path or Google Drive.
    Validates that the requested path resolves within one of the known
    ingest directories to prevent path-traversal attacks, then that the
    caller may see the document the file belongs to (see _file_permitted) -
    pass `table` and `id` when showing a known document, for a direct check.
    A file the caller may not see answers 404, same as a missing one.
    """
    file_path = Path(path).resolve()
    if not any(file_path.is_relative_to(root) for root in _SAFE_FILE_ROOTS):
        raise HTTPException(status_code=403, detail=t("common.access_denied", current_user.locale))
    paths = list(dict.fromkeys([path, str(file_path)]))
    async with _engine().connect() as conn:
        permitted = await _file_permitted(conn, current_user, paths, table, id)
        if permitted and not file_path.exists() and await sync_files.file_pending(conn, path):
            raise sync_files.pending_exception(current_user.locale)
        if not permitted or not file_path.exists():
            raise HTTPException(
                status_code=404, detail=t("common.file_not_found", current_user.locale)
            )
        # Anything outside the page-image directory is an uploaded original
        # (or a path/Drive-ingested source file) - serving it is a download.
        # Attributed to the named document only when that document really
        # references the file, so a caller can't pin it on another one.
        named = None
        is_original = not file_path.is_relative_to(_IMAGES_ROOT)
        if is_original:
            tables_cols = await _get_tables_columns(conn, current_user.tenant_slug)
            named = await _named_document_has_file(
                conn, current_user, tables_cols, paths, table, id
            )
    if is_original and await first_in_window(
        f"download:{current_user.id}:{file_path}", ACCESS_AUDIT_WINDOW_SECONDS
    ):
        await log_action(
            action="document_downloaded",
            user_id=current_user.id,
            user_email=current_user.email,
            table_name=named,
            document_id=id if named else None,
            details={"via": "image", "file": file_path.name},
            ip_address=client_ip(request),
        )
    return FileResponse(file_path)
