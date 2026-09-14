"""
Document browser — queries the dynamically-inferred PostgreSQL tables created
by the ingestion pipeline.  Table names are always discovered via
information_schema; they are never hardcoded or taken raw from user input.

Endpoints:
  GET /api/db/documents              — paginated list with filters & full-text search
  GET /api/db/documents/{tbl}/{id}   — full field detail for one document
  GET /api/db/types                  — table inventory with counts
  GET /api/db/image                  — serve a processed image, original PDF,
                                        or path/Drive-ingested source file
"""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from auth import get_current_user
from cache import _path_key_builder
from fastapi_cache.decorator import cache
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

async def _get_tables_columns(conn) -> dict[str, set[str]]:
    """
    Return {table_name: {col, ...}} for every ingest-created table.
    Ingest tables are identified by having a 'source_image_path' column
    and not being one of the infrastructure tables.
    """
    result = await conn.execute(text("""
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.table_name NOT IN ('batches', 'batch_documents')
          AND EXISTS (
              SELECT 1 FROM information_schema.columns c2
              WHERE c2.table_schema = 'public'
                AND c2.table_name = c.table_name
                AND c2.column_name = 'source_image_path'
          )
        ORDER BY c.table_name, c.column_name
    """))
    tables: dict[str, set[str]] = {}
    for row in result:
        tables.setdefault(row[0], set()).add(row[1])
    return tables


# ── UNION query builder ───────────────────────────────────────────────────────

_OPTIONAL_COLS = [
    "document_type", "reference_number", "date",
    "organisation", "destination_or_subject", "signatory",
]
_SEARCH_COLS = ["reference_number", "organisation", "destination_or_subject", "signatory"]


def _per_table_select(
    table: str,
    cols: set[str],
    *,
    q: Optional[str],
    confidence: Optional[str],
    review_status: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> Optional[str]:
    """Build the per-table SELECT fragment for the UNION.  Returns None when
    the table should be excluded (e.g. q is set but table has no text cols)."""
    searchable = [c for c in _SEARCH_COLS if c in cols]
    if q and not searchable:
        return None

    sel = ["id::text AS id", f"'{table}'::text AS table_name"]
    for col in _OPTIONAL_COLS:
        sel.append(f"{col}::text AS {col}" if col in cols else f"NULL::text AS {col}")
    sel += [
        "confidence::text    AS confidence",
        "review_status::text AS review_status",
        "ingested_at::text   AS ingested_at",
        "source_image_path",
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

    where_str = ("WHERE " + " AND ".join(where)) if where else ""
    return f'SELECT {", ".join(sel)}\nFROM "{table}"\n{where_str}'


async def _run_list_query(
    conn,
    tables_cols: dict[str, set[str]],
    *,
    only_table: Optional[str],
    q: Optional[str],
    confidence: Optional[str],
    review_status: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    page: int,
    page_size: int,
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
            date_from=date_from, date_to=date_to,
        )
        for tbl, cols in scope.items()
    ]
    parts = [p for p in parts if p]
    if not parts:
        return [], 0

    union_sql  = "\nUNION ALL\n".join(parts)
    params: dict = {"page_size": page_size, "offset": (page - 1) * page_size}
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
async def list_types(current_user: str = Depends(get_current_user)):
    """
    Discover all ingest-created tables via information_schema and return
    each table's name, document type label, row count, and last ingestion
    timestamp. Used to populate the type-filter dropdown in the document browser.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
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
            row = await conn.execute(
                text(
                    f'SELECT COUNT(*), MAX(ingested_at), '
                    f'{doc_type_expr} FROM "{table}"'
                )
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
    current_user:  str           = Depends(get_current_user),
):
    """
    Paginated document list.
    - document_type: restrict to a single table (sanitized → table name)
    - q: full-text search (GIN index) across reference_number, organisation, destination, signatory
    - confidence / review_status: equality filter
    - date_from / date_to: filter on ingested_at (ISO 8601 dates)
    """
    only_table = _sanitize(document_type) if document_type else None

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)

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
        )

    return {"total": total, "page": page, "page_size": page_size, "results": results}


@router.get("/documents/{table_name}/{doc_id}", summary="Document detail")
async def get_document_detail(
    table_name:   str,
    doc_id:       str,
    current_user: str = Depends(get_current_user),
):
    """
    Return every column value for one document.
    The table name is validated against information_schema before querying.
    JSONB columns (arrays) are returned as proper JSON arrays.
    Use this endpoint to populate the review form and the document detail page.
    """
    safe = _sanitize(table_name)

    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
        if safe not in tables_cols:
            raise HTTPException(status_code=404, detail=f"Table '{safe}' not found")

        result = await conn.execute(
            text(f'SELECT * FROM "{safe}" WHERE id = CAST(:id AS uuid)'),
            {"id": doc_id},
        )
        row = result.mappings().one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")

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


@router.get("/schema")
@limiter.limit("30/minute")
@cache(expire=30, namespace="schema", key_builder=_path_key_builder)
async def get_schema(request: Request, current_user: str = Depends(get_current_user)):
    """
    Return full schema: tables, columns (with type/nullable), row counts,
    last ingested, and foreign key relationships.
    Everything discovered from information_schema — no table names hardcoded.
    """
    async with _engine().connect() as conn:
        tables_cols = await _get_tables_columns(conn)
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
            stat = await conn.execute(
                text(f'SELECT COUNT(*), MAX(ingested_at), {doc_type_expr} FROM "{table_name}"')
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
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)
