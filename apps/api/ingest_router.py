"""
Document ingestion pipeline.

Endpoints:
  POST /api/ingest/upload              — multipart files → batch_id (async processing)
  POST /api/ingest/path                — server path or Google Drive → batch_id
  GET  /api/ingest/status/{batch_id}   — per-document progress

Three-stage parallel pipeline:

  Stage 1 — Preprocessing pool (asyncio.Semaphore(4))
    PDF → PNG · orientation correction · flag-stripe removal · resize to 1600 px
    Classifier: Tesseract on 400 px thumbnail; char_count < 50 → out_of_scope
    Output: _PreparedDoc placed on _preprocessed_queue

  Stage 2 — VLM single worker
    Sends one document at a time to Ollama (qwen2.5vl:7b).
    120 s timeout per document; timeouts and parse errors treated as crashes.
    Output: _VlmResult placed on _vlm_queue

  Stage 3 — Post-processing pool (asyncio.Semaphore(4))
    Schema inference: CREATE / ALTER table in PostgreSQL
    Three-tier INSERT: auto_approved / review_required / manual_entry
    Updates batch_documents status after each document.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

import cv2
import httpx
import numpy as np
import pytesseract
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from audit import log_action
from auth import get_current_user, require_admin
from cache import invalidate_cache
from rate_limit import limiter

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DATA_DIR    = Path(os.getenv("DATA_DIR", "./data"))
IMAGES_DIR  = DATA_DIR / "images"
UPLOADS_DIR = DATA_DIR / "uploads"

BASE     = Path(__file__).parent
DOCS_DIR = BASE / "documents" / "anonymized_docs"

ALLOWED_EXTS  = {".jpg", ".jpeg", ".png", ".pdf"}
VLM_MAX_SIZE  = 1600
THUMB_SIZE    = 400
MIN_INK_FRACTION = 0.002  # >0.2% non-background pixels counts as "has content"

# Per-page VLM call timeout. The default (120s) was tuned for short
# administrative-document fields; a schema that asks for more output per
# page (e.g. a list of dictionary entries) needs more generation time and
# may need this raised — see README.md "Customizing the extraction schema".
VLM_PAGE_TIMEOUT = float(os.getenv("VLM_PAGE_TIMEOUT_SECONDS") or 120.0)

# For documents typeset in two independent side-by-side columns (e.g. a
# dictionary — NOT parallel-text translation, which needs both columns
# visible together to pair correctly), split each page down the middle
# before VLM extraction. Roughly halves the content — and therefore the
# generation time — per VLM call. See README.md "Customizing the
# extraction schema".
SPLIT_PAGE_COLUMNS = (os.getenv("SPLIT_PAGE_COLUMNS") or "").strip().lower() in ("1", "true", "yes")

# Admins using POST /api/ingest/path can only read from this directory tree.
# Prevents arbitrary filesystem traversal even by authenticated admin accounts.
INGEST_ROOT = Path(os.getenv("INGEST_ROOT", "/data/ingest")).resolve()

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))  # 50 MB

# ── DB setup (lazily initialised in startup()) ────────────────────────────────
# Not created at import time so that api_server can be imported in tests
# without asyncpg being present.

engine: Optional[object]       = None
SessionLocal: Optional[object] = None


def _engine():
    if engine is None:
        raise RuntimeError("DB engine not initialised — startup() not called")
    return engine  # type: ignore[return-value]


def _session():
    if SessionLocal is None:
        raise RuntimeError("SessionLocal not initialised — startup() not called")
    return SessionLocal  # type: ignore[return-value]

_INIT_DDL = """
CREATE TABLE IF NOT EXISTS sdai_users (
    id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT    UNIQUE NOT NULL,
    hashed_password TEXT    NOT NULL,
    full_name       TEXT,
    role            TEXT    DEFAULT 'reviewer' CHECK (role IN ('admin', 'reviewer')),
    created_at      TIMESTAMPTZ DEFAULT now(),
    is_active       BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS sdai_token_blocklist (
    jti        TEXT        PRIMARY KEY,
    expired_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sdai_audit_log (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES sdai_users(id) ON DELETE SET NULL,
    user_email  TEXT,
    action      TEXT        NOT NULL,
    table_name  TEXT,
    document_id UUID,
    details     JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sdai_audit_log_created_at_idx ON sdai_audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS sdai_audit_log_action_idx     ON sdai_audit_log (action);
CREATE INDEX IF NOT EXISTS sdai_audit_log_user_id_idx    ON sdai_audit_log (user_id);

CREATE TABLE IF NOT EXISTS batches (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ      DEFAULT now(),
    source_type TEXT             NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id        UUID        REFERENCES batches(id),
    filename        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',
    document_type   TEXT,
    confidence      TEXT,
    processing_time REAL,
    error_message   TEXT,
    image_path      TEXT,
    source_path     TEXT,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);

-- One row per page of a document, updated live as Stage 2 works through
-- them, so multi-page (especially many-page) documents have real progress
-- visibility instead of a single all-or-nothing status.
CREATE TABLE IF NOT EXISTS batch_document_pages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_document_id   UUID        REFERENCES batch_documents(id) ON DELETE CASCADE,
    page_number         INTEGER     NOT NULL,
    source_page_number  INTEGER,
    image_path          TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'pending',
    error_message       TEXT,
    processing_time     REAL,
    fields              JSONB,
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_batch_document_pages_doc
    ON batch_document_pages (batch_document_id);
CREATE INDEX IF NOT EXISTS idx_batch_document_pages_status
    ON batch_document_pages (status);

-- Backfill for installations where these tables already existed before
-- source_path / source_page_number were added (CREATE TABLE IF NOT
-- EXISTS above is a no-op on an existing table).
ALTER TABLE batch_documents ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE batch_document_pages ADD COLUMN IF NOT EXISTS source_page_number INTEGER;
"""


async def _init_db() -> None:
    async with _engine().begin() as conn:
        # asyncpg's extended query protocol can't prepare multiple commands
        # in one statement, so each DDL statement is executed separately.
        for statement in _INIT_DDL.split(";"):
            statement = statement.strip()
            if statement:
                await conn.execute(text(statement))
        # Purge expired blocklist entries on each startup
        await conn.execute(
            text("DELETE FROM sdai_token_blocklist WHERE expired_at < NOW()")
        )
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── Pipeline queues, semaphores, and inter-stage types ───────────────────────

# Stage 1 input — unchanged external contract; API endpoints call _queue.put()
_queue: asyncio.Queue = asyncio.Queue()

# Stage 1 → Stage 2: preprocessed images waiting for VLM
_preprocessed_queue: asyncio.Queue = asyncio.Queue()

# Stage 2 → Stage 3: VLM results (including errors) waiting for DB writes
_vlm_queue: asyncio.Queue = asyncio.Queue()

# Worker tasks — created in startup(), cancelled in shutdown()
_worker_tasks: list[asyncio.Task] = []

# Stage 1 and Stage 3 each allow up to 4 concurrent tasks.
# Stage 2 is intentionally single-threaded to avoid saturating Ollama.
_preprocess_sem = asyncio.Semaphore(4)
_postprocess_sem = asyncio.Semaphore(4)


class _PreparedDoc(NamedTuple):
    doc_id:       str
    batch_id:     str
    dest_paths:   list[Path]        # one preprocessed image per page
    page_ids:     list[str]         # batch_document_pages row id, parallel to dest_paths
    pdf_path:     Optional[Path]    # original PDF, if the source was a PDF
    filename:     str
    t0:           float
    content_hash: str


class _VlmResult(NamedTuple):
    doc_id:       str
    batch_id:     str
    dest_paths:   list[Path]
    pdf_path:     Optional[Path]
    filename:     str
    t0:           float
    fields:       dict         # reconciled fields across all pages, or {} on total failure
    error:        Optional[str]  # None on success; message when every page crashed/timed out
    content_hash: str


async def startup() -> None:
    global engine, SessionLocal
    engine       = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    await _init_db()
    global _worker_tasks
    _worker_tasks = [
        asyncio.create_task(_stage1_worker()),
        asyncio.create_task(_stage2_worker()),
        asyncio.create_task(_stage3_worker()),
    ]


async def shutdown() -> None:
    for task in _worker_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── VLM prompt (configurable per deployment) ─────────────────────────────────
#
# The prompt defines the extraction schema for your document corpus and is
# inherently domain-specific — a different document type (invoices, a
# lexicon, land titles, ...) needs different fields. Override it without a
# code change or rebuild via:
#
#   VLM_PROMPT_FILE  — path to a text file containing the full prompt.
#                       Put it under documents/ (already volume-mounted) so
#                       it survives image rebuilds, e.g.
#                       VLM_PROMPT_FILE=/app/documents/prompts/lexicon.txt
#   VLM_LIST_FIELDS  — comma-separated names of fields that should be
#                       unioned across pages during multi-page reconciliation
#                       (see _reconcile_pages) rather than the default
#                       "first non-empty value wins". Must match whatever
#                       array-valued keys your custom prompt's schema uses.
#
# See README.md "Customizing the extraction schema" for a full walkthrough.
#
# Kept in sync manually with apps/api/prompts/examples/admin_document.txt,
# a tracked reference copy of this exact string — if you edit one, edit
# the other too.

_DEFAULT_VLM_PROMPT = """This is a Chadian government administrative document.
Extract the following fields exactly as written in the document.
Return ONLY a JSON object:

{
  "document_type": "the document type title as written e.g. ORDRE DE MISSION, ARRETE, DECISION, CORRESPONDANCE",
  "reference_number": "the reference number as written",
  "date": "the date as written",
  "person_names": ["full names of individuals mentioned"],
  "destination_or_subject": "destination city or subject of document",
  "organisation": "the ministry or organisation name",
  "signatory": "name of the person who signed",
  "budget_line": "budget imputation if present",
  "language": "fr or ar or bilingual",
  "quality_issues": ["list from: upside_down, flag_stripes, handwriting, faded_ink, security_background, typewriter"],
  "extraction_confidence": "high or medium or low"
}"""


def _load_vlm_prompt() -> str:
    prompt_file = os.getenv("VLM_PROMPT_FILE")
    if not prompt_file:
        return _DEFAULT_VLM_PROMPT
    path = Path(prompt_file)
    if not path.is_file():
        raise RuntimeError(f"VLM_PROMPT_FILE={prompt_file!r} does not exist")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"VLM_PROMPT_FILE={prompt_file!r} is empty")
    return text


_VLM_PROMPT = _load_vlm_prompt()

# Fields that accumulate across pages rather than being overwritten.
# Uses `or` rather than os.getenv's default param: docker-compose always
# sets this var (to "" when unset in .env), and the default param only
# kicks in when a var is truly absent, not merely empty.
_VLM_LIST_FIELDS = {
    f.strip()
    for f in (os.getenv("VLM_LIST_FIELDS") or "person_names,quality_issues").split(",")
    if f.strip()
}
_CONFIDENCE_RANK = {"high": 2, "medium": 1, "low": 0}

# Fields that don't count as "this page contributed content" on their own —
# document_type is typically a constant repeated on every page (including
# blank/cover/TOC pages), so its mere presence shouldn't count a page as
# having extracted anything.
_NON_CONTENT_FIELDS = {"document_type", "extraction_confidence"}


def _reconcile_pages(page_fields: list[dict]) -> dict:
    """Merge one VLM extraction per page into a single document-level record.

    Singular fields (reference_number, date, organisation, ...) take the
    first non-empty value found, since letterhead/metadata is usually on
    page 1 and repeating it verbatim on every page would just overwrite
    with the same value. List fields are unioned across all pages in
    order. Confidence is the worst (most conservative) tier seen across
    pages that actually contributed content, so a single problematic
    content page still routes the whole document to human review instead
    of being masked by a confident page 1 — but a page that legitimately
    found nothing (a cover page, blank page, table of contents) doesn't
    drag the whole document's confidence down just because the model
    reported "low" for having nothing to extract.
    """
    merged: dict = {}
    worst_confidence: Optional[str] = None

    for fields in page_fields:
        page_confidence = fields.get("extraction_confidence")
        contributed = False

        for key, value in fields.items():
            if key == "extraction_confidence":
                continue
            if key in _VLM_LIST_FIELDS:
                items = value or []
                if items:
                    contributed = True
                existing = merged.setdefault(key, [])
                for item in items:
                    if item not in existing:
                        existing.append(item)
            elif value not in (None, "", []):
                if key not in _NON_CONTENT_FIELDS:
                    contributed = True
                if key not in merged:
                    merged[key] = value

        if contributed and page_confidence in _CONFIDENCE_RANK:
            if (
                worst_confidence is None
                or _CONFIDENCE_RANK[page_confidence] < _CONFIDENCE_RANK[worst_confidence]
            ):
                worst_confidence = page_confidence

    merged["extraction_confidence"] = worst_confidence or "low"
    return merged


# ── Preprocessing ─────────────────────────────────────────────────────────────

def _pdf_to_png(pdf_path: Path) -> list[Path]:
    """Convert each PDF page to a PNG saved beside the PDF."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise RuntimeError("pdf2image not installed; cannot process PDFs")

    pages = convert_from_path(str(pdf_path), dpi=200)
    out_paths = []
    for i, page in enumerate(pages):
        out_path = pdf_path.with_name(f"{pdf_path.stem}_page{i+1:03d}.png")
        page.save(str(out_path), "PNG")
        out_paths.append(out_path)
    return out_paths


def _pdf_page_to_png(pdf_path: Path, page_number: int) -> Path:
    """Render a single page of a PDF (1-indexed) to a PNG beside the PDF,
    without re-rendering the whole document — used to reload just one
    page from source instead of re-uploading the whole file."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise RuntimeError("pdf2image not installed; cannot process PDFs")

    pages = convert_from_path(
        str(pdf_path), dpi=200, first_page=page_number, last_page=page_number
    )
    if not pages:
        raise RuntimeError(f"PDF page {page_number} not found in {pdf_path}")
    out_path = pdf_path.with_name(f"{pdf_path.stem}_page{page_number:03d}_reload.png")
    pages[0].save(str(out_path), "PNG")
    return out_path


def _correct_orientation(img: Image.Image) -> Image.Image:
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        angle = osd.get("rotate", 0)
        if angle:
            img = img.rotate(-angle, expand=True)
    except Exception:
        pass
    return img


def _remove_flag_stripes(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGB"))
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

    # Chadian flag colours: blue, yellow, red
    blue   = cv2.inRange(hsv, (100, 80,  80),  (130, 255, 255))
    yellow = cv2.inRange(hsv, ( 20, 100, 100), ( 35, 255, 255))
    red1   = cv2.inRange(hsv, (  0, 100, 100), ( 10, 255, 255))
    red2   = cv2.inRange(hsv, (170, 100, 100), (180, 255, 255))
    mask   = blue | yellow | red1 | red2

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (12, 12))
    mask   = cv2.dilate(mask, kernel)
    arr[mask > 0] = [255, 255, 255]
    return Image.fromarray(arr)


def _resize_max(img: Image.Image, max_dim: int) -> Image.Image:
    w, h    = img.size
    longest = max(w, h)
    if longest <= max_dim:
        return img
    scale = max_dim / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _detect_column_gutter(img: Image.Image) -> Optional[int]:
    """Look for a vertical whitespace gutter near the horizontal center of
    a page — the signature of a genuine two-column layout, as opposed to
    a single full-width page (cover, TOC, intro) that just happens to
    share the same dimensions. Returns the x-coordinate to split at, or
    None if no clear gutter is found (the page should stay whole).
    """
    gray = np.array(img.convert("L"))
    h, w = gray.shape

    # Ignore top/bottom margins (headers, footers, page numbers) so stray
    # marks there don't break up the gutter search.
    band = gray[int(h * 0.08): int(h * 0.92), :]
    ink_per_col = (band < 250).mean(axis=0)

    # Search for the gutter within the middle portion of the page width —
    # a real two-column split lands close to center, not near an edge.
    lo, hi = int(w * 0.35), int(w * 0.65)
    is_gutter = ink_per_col[lo:hi] < 0.01

    # Longest contiguous run of near-zero-ink columns in that band.
    best_start, best_len, run_start = None, 0, None
    for i, gutter_col in enumerate(is_gutter):
        if gutter_col:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            run_len = i - run_start
            if run_len > best_len:
                best_start, best_len = run_start, run_len
            run_start = None
    if run_start is not None and len(is_gutter) - run_start > best_len:
        best_start, best_len = run_start, len(is_gutter) - run_start

    min_gutter_px = max(8, int(w * 0.01))
    if best_start is None or best_len < min_gutter_px:
        return None

    return lo + best_start + best_len // 2


def _preprocess(src: Path) -> list[Image.Image]:
    """Full preprocessing pipeline — runs in a thread executor.

    Returns two images (left/right column) when SPLIT_PAGE_COLUMNS is
    enabled AND this specific page is detected to actually have a
    two-column layout (a whitespace gutter near center) — for documents
    typeset in independent side-by-side columns (e.g. a dictionary, each
    entry self-contained within its column) rather than parallel-text
    translation that needs both columns visible together to pair
    correctly. A full-width page (cover, TOC, intro) has no such gutter
    and is returned whole, even when the flag is on — not every page of
    a two-column document is itself two-column.
    """
    img = Image.open(src).convert("RGB")
    img = _correct_orientation(img)
    img = _remove_flag_stripes(img)

    if SPLIT_PAGE_COLUMNS:
        split_x = _detect_column_gutter(img)
        if split_x is not None:
            w, h = img.size
            halves = [img.crop((0, 0, split_x, h)), img.crop((split_x, 0, w, h))]
            return [_resize_max(half, VLM_MAX_SIZE) for half in halves]

    return [_resize_max(img, VLM_MAX_SIZE)]


# ── Document classifier ───────────────────────────────────────────────────────

def _has_visible_content(img_path: Path) -> bool:
    """Returns True unless the page is essentially blank (near-uniform
    background, no visible ink). Checked via pixel coverage rather than
    OCR text extraction, so it works regardless of script or language —
    an OCR-based check (e.g. Tesseract with a fixed language list) would
    wrongly classify a page written in an unrecognized script as blank
    and silently drop real content.
    """
    try:
        img = Image.open(img_path).convert("L")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        arr = np.array(img)
        ink_fraction = (arr < 250).mean()
        return ink_fraction > MIN_INK_FRACTION
    except Exception:
        return True  # fail open — never silently drop a page over a processing error


# ── VLM extraction (async, Ollama REST) ───────────────────────────────────────

async def _run_vlm(img_path: Path) -> dict:
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    async with httpx.AsyncClient(timeout=max(10.0, VLM_PAGE_TIMEOUT - 5.0)) as client:
        resp = await client.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": "qwen2.5vl:7b",
                "messages": [
                    {"role": "user", "content": _VLM_PROMPT, "images": [img_b64]}
                ],
                "stream": False,
            },
        )
        resp.raise_for_status()
        raw_text = resp.json()["message"]["content"].strip()

    # Strip markdown code fences
    raw_text = re.sub(r"^```json\s*", "", raw_text)
    raw_text = re.sub(r"^```\s*",     "", raw_text)
    raw_text = re.sub(r"\s*```$",     "", raw_text)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"_parse_error": True, "_raw": raw_text}


# ── Content-hash deduplication ────────────────────────────────────────────────

def _compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def _find_duplicate(content_hash: str) -> dict | None:
    """Return {table_name, id} for the first doc with this hash, or None."""
    async with _engine().connect() as conn:
        tables_result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.columns"
                " WHERE column_name = 'content_hash' AND table_schema = 'public'"
            )
        )
        tables = [row[0] for row in tables_result]

    for table_name in tables:
        async with _engine().connect() as conn:
            row = await conn.execute(
                text(
                    f'SELECT id FROM "{table_name}"'
                    f" WHERE content_hash = :h AND review_status != 'manual_entry'"
                    f" LIMIT 1"
                ),
                {"h": content_hash},
            )
            match = row.one_or_none()
            if match:
                return {"table_name": table_name, "id": str(match[0])}
    return None


async def _delete_document_by_batch_document_id(batch_document_id: str) -> bool:
    """Find and delete the extracted document row for one upload — found
    via its batch_document_id, regardless of which document_type table it
    landed in — so the same file can be re-uploaded without tripping the
    content-hash duplicate check. Returns True if a row was found and
    deleted. Does not touch batch/page history (batch_documents,
    batch_document_pages), only the per-type table's stored extraction."""
    async with _engine().connect() as conn:
        tables_result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.columns"
                " WHERE column_name = 'batch_document_id' AND table_schema = 'public'"
            )
        )
        tables = [row[0] for row in tables_result]

    for table_name in tables:
        async with _engine().begin() as conn:
            result = await conn.execute(
                text(
                    f'DELETE FROM "{table_name}"'
                    f" WHERE batch_document_id = CAST(:bd AS uuid) RETURNING id"
                ),
                {"bd": batch_document_id},
            )
            if result.rowcount > 0:
                return True
    return False


# ── Schema inference + storage ────────────────────────────────────────────────

def _sanitize_identifier(name: str, max_len: int = 63) -> str:
    name = name.lower().strip()
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    name = re.sub(r"^[0-9_]+", "", name)
    return (name or "document")[:max_len]


def _pg_type(key: str, value) -> str:
    if isinstance(value, list):
        return "JSONB"
    if isinstance(value, bool):
        return "BOOLEAN"
    return "TEXT"


# Ordered tuple used both for membership testing and for building the FTS expression.
# Must match the column names produced by _sanitize_identifier on the VLM output keys.
_FTS_COLS = ("reference_number", "organisation", "destination_or_subject", "signatory")


async def _ensure_table(conn, table_name: str, fields: dict) -> str:
    """
    Ensure the table exists with all required columns.
    Returns 'created', 'altered', or 'unchanged' for audit logging.
    Creates / maintains indexes after DDL.
    """
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_name = :t)"
        ),
        {"t": table_name},
    )
    exists = result.scalar()

    if not exists:
        base_cols = [
            "id               UUID PRIMARY KEY DEFAULT gen_random_uuid()",
            "source_image_path TEXT",
            "source_pdf_path  TEXT",
            "page_image_paths JSONB",
            "batch_id         UUID",
            "batch_document_id UUID",
            "ingested_at      TIMESTAMPTZ DEFAULT now()",
            "confidence       TEXT",
            "review_status    TEXT DEFAULT 'pending'",
            "content_hash     TEXT",
        ]
        field_cols = [
            f"{_sanitize_identifier(k)} {_pg_type(k, v)}"
            for k, v in fields.items()
            if not k.startswith("_") and k != "extraction_confidence"
        ]
        ddl = (
            f'CREATE TABLE IF NOT EXISTS "{table_name}"'
            f" ({', '.join(base_cols + field_cols)})"
        )
        await conn.execute(text(ddl))

        # B-tree indexes on the columns present in every inferred table
        for suffix, expr in [
            ("ingested_at",   "ingested_at DESC"),
            ("review_status", "review_status"),
            ("confidence",    "confidence"),
        ]:
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_{suffix}"
                f' ON "{table_name}" ({expr})'
            ))

        await conn.execute(text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_content_hash"
            f' ON "{table_name}" (content_hash)'
        ))

        # GIN full-text index over whichever FTS columns were actually created.
        # The VLM prompt always requests all four, but guard against missing keys.
        created_cols = {_sanitize_identifier(k) for k, v in fields.items()
                        if not k.startswith("_") and k != "extraction_confidence"}
        fts_present = [c for c in _FTS_COLS if c in created_cols]
        if fts_present:
            coalesces = " || ' ' || ".join(f"COALESCE({c},'')" for c in fts_present)
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_fts"
                f' ON "{table_name}" USING gin'
                f"(to_tsvector('french', {coalesces}))"
            ))

        return "created"

    # Add any new columns that don't yet exist
    existing = {
        row[0]
        for row in (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = :t"
                ),
                {"t": table_name},
            )
        )
    }
    altered = False
    fts_col_added = False
    for k, v in fields.items():
        if k.startswith("_") or k == "extraction_confidence":
            continue
        col = _sanitize_identifier(k)
        if col not in existing:
            await conn.execute(
                text(
                    f'ALTER TABLE "{table_name}"'
                    f" ADD COLUMN IF NOT EXISTS {col} {_pg_type(k, v)}"
                )
            )
            altered = True
            if col in _FTS_COLS:
                fts_col_added = True

    for col, pg_type in (
        ("content_hash",      "TEXT"),
        ("source_pdf_path",   "TEXT"),
        ("page_image_paths",  "JSONB"),
        ("batch_document_id", "UUID"),
    ):
        if col not in existing:
            await conn.execute(text(
                f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS {col} {pg_type}'
            ))
            altered = True

    await conn.execute(text(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_content_hash"
        f' ON "{table_name}" (content_hash)'
    ))

    if fts_col_added:
        # A column that contributes to the FTS expression was added.
        # Drop and rebuild the GIN index so the new column is included.
        await conn.execute(text(f"DROP INDEX IF EXISTS idx_{table_name}_fts"))
        all_cols = existing | {_sanitize_identifier(k) for k, v in fields.items()
                               if not k.startswith("_") and k != "extraction_confidence"}
        fts_present = [c for c in _FTS_COLS if c in all_cols]
        if fts_present:
            coalesces = " || ' ' || ".join(f"COALESCE({c},'')" for c in fts_present)
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_fts"
                f' ON "{table_name}" USING gin'
                f"(to_tsvector('french', {coalesces}))"
            ))

    return "altered" if altered else "unchanged"


def _serialize(value) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value) if value is not None else None


async def _store_extraction(
    batch_id: str,
    image_path: str,
    fields: dict,
    confidence: str,
    review_status: str,
    content_hash: str | None = None,
    source_pdf_path: str | None = None,
    page_image_paths: list[str] | None = None,
    batch_document_id: str | None = None,
) -> None:
    doc_type   = fields.get("document_type") or "document"
    table_name = _sanitize_identifier(doc_type)

    async with _engine().begin() as conn:
        schema_event = await _ensure_table(conn, table_name, fields)

        col_names = ["source_image_path", "batch_id", "confidence", "review_status"]
        col_vals  = [image_path, batch_id, confidence, review_status]

        if content_hash is not None:
            col_names.append("content_hash")
            col_vals.append(content_hash)

        if source_pdf_path is not None:
            col_names.append("source_pdf_path")
            col_vals.append(source_pdf_path)

        if page_image_paths is not None:
            col_names.append("page_image_paths")
            col_vals.append(json.dumps(page_image_paths, ensure_ascii=False))

        if batch_document_id is not None:
            col_names.append("batch_document_id")
            col_vals.append(batch_document_id)

        for k, v in fields.items():
            if k.startswith("_") or k == "extraction_confidence":
                continue
            col_names.append(_sanitize_identifier(k))
            col_vals.append(_serialize(v))

        placeholders = ", ".join(f":p{i}" for i in range(len(col_vals)))
        col_clause   = ", ".join(f'"{c}"' for c in col_names)
        params       = {f"p{i}": v for i, v in enumerate(col_vals)}

        result = await conn.execute(
            text(
                f'INSERT INTO "{table_name}" ({col_clause})'
                f' VALUES ({placeholders}) RETURNING id'
            ),
            params,
        )
        doc_id = str(result.scalar())

    # ── Audit logging (outside the main transaction; never raises) ────────────
    if schema_event == "created":
        await log_action(
            action="schema_created",
            table_name=table_name,
            details={"document_type": doc_type},
        )
        await invalidate_cache("schema", "types")
    elif schema_event == "altered":
        await log_action(
            action="schema_altered",
            table_name=table_name,
            details={"document_type": doc_type},
        )
        await invalidate_cache("schema")

    await log_action(
        action="document_ingested",
        table_name=table_name,
        document_id=doc_id,
        details={
            "document_type": doc_type,
            "confidence":    confidence,
            "review_status": review_status,
            "batch_id":      batch_id,
        },
    )


# ── DB helpers for batch_documents ────────────────────────────────────────────

async def _update_doc_status(
    doc_id: str,
    status: str,
    *,
    document_type: str | None = None,
    confidence: str | None = None,
    processing_time: float | None = None,
    error_message: str | None = None,
    image_path: str | None = None,
) -> None:
    async with _session()() as sess:
        await sess.execute(
            text(
                "UPDATE batch_documents SET status=:s, document_type=:dt,"
                " confidence=:c, processing_time=:pt, error_message=:em,"
                " image_path=:ip WHERE id=:id"
            ),
            {
                "s":  status,
                "dt": document_type,
                "c":  confidence,
                "pt": processing_time,
                "em": error_message,
                "ip": image_path,
                "id": doc_id,
            },
        )
        await sess.commit()


# ── DB helpers for batch_document_pages ───────────────────────────────────────

async def _create_page_rows(
    batch_document_id: str,
    page_paths: list[Path],
    source_page_numbers: Optional[list[int]] = None,
) -> list[str]:
    """Insert one pending row per page; returns their ids in page order.

    source_page_numbers tracks which page of the *original* source
    document each row came from — when column-splitting produces two
    halves from one original page, both rows share the same source page
    number, distinct from `page_number` (the sequential position among
    this document's *output* pages). Defaults to 1:1 with page order when
    not given (single-page-per-original-page documents).
    """
    if source_page_numbers is None:
        source_page_numbers = list(range(1, len(page_paths) + 1))
    async with _session()() as sess:
        page_ids: list[str] = []
        for i, (path, source_n) in enumerate(zip(page_paths, source_page_numbers)):
            result = await sess.execute(
                text(
                    "INSERT INTO batch_document_pages"
                    " (batch_document_id, page_number, source_page_number, image_path)"
                    " VALUES (:bd, :n, :sn, :p) RETURNING id"
                ),
                {"bd": batch_document_id, "n": i + 1, "sn": source_n, "p": str(path)},
            )
            page_ids.append(str(result.scalar()))
        await sess.commit()
    return page_ids


async def _update_page_status(
    page_id: str,
    status: str,
    *,
    error_message: str | None = None,
    processing_time: float | None = None,
    fields: dict | None = None,
) -> None:
    async with _session()() as sess:
        await sess.execute(
            text(
                "UPDATE batch_document_pages SET status=:s, error_message=:em,"
                " processing_time=:pt, fields=:f, updated_at=now() WHERE id=:id"
            ),
            {
                "s":  status,
                "em": error_message,
                "pt": processing_time,
                "f":  json.dumps(fields, ensure_ascii=False) if fields is not None else None,
                "id": page_id,
            },
        )
        await sess.commit()


# ── Stage 1: Preprocessing pool ───────────────────────────────────────────────

async def _stage1_process(
    doc_id: str, batch_id: str, src_path: Path, filename: str, content_hash: str
) -> None:
    """Preprocess every page of one document under the semaphore; push to
    _preprocessed_queue. The original PDF (if any) is kept on disk and
    referenced so it stays available for review/download."""
    async with _preprocess_sem:
        t0 = time.monotonic()
        await _update_doc_status(doc_id, "processing")
        try:
            pdf_path = None
            if src_path.suffix.lower() == ".pdf":
                pdf_path  = src_path
                page_srcs = await asyncio.to_thread(_pdf_to_png, src_path)
                if not page_srcs:
                    page_srcs = [src_path]
            else:
                page_srcs = [src_path]

            stem = Path(filename).stem
            multi_page = len(page_srcs) > 1
            dest_dir = IMAGES_DIR / batch_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_paths: list[Path] = []
            source_page_numbers: list[int] = []
            for i, page_src in enumerate(page_srcs):
                page_stem = f"{stem}_page{i+1:03d}" if multi_page else stem
                processed = await asyncio.to_thread(_preprocess, page_src)
                for j, img in enumerate(processed):
                    suffix = chr(ord("a") + j) if len(processed) > 1 else ""
                    dest_path = dest_dir / f"{page_stem}{suffix}.png"
                    await asyncio.to_thread(img.save, str(dest_path), "PNG")
                    dest_paths.append(dest_path)
                    # Both halves of a split page share the same source
                    # page number — they came from the same original page.
                    source_page_numbers.append(i + 1)

            # Drop pages with no visible ink (genuinely blank pages); a
            # page here just contributes nothing to the reconciled result.
            content_flags = await asyncio.gather(
                *(asyncio.to_thread(_has_visible_content, p) for p in dest_paths)
            )
            kept_paths = [p for p, ok in zip(dest_paths, content_flags) if ok]
            kept_source_numbers = [
                n for n, ok in zip(source_page_numbers, content_flags) if ok
            ]

            if not kept_paths:
                await _update_doc_status(
                    doc_id, "out_of_scope",
                    image_path=str(dest_paths[0]),
                    processing_time=round(time.monotonic() - t0, 2),
                )
                return

            page_ids = await _create_page_rows(doc_id, kept_paths, kept_source_numbers)
            await _preprocessed_queue.put(
                _PreparedDoc(doc_id, batch_id, kept_paths, page_ids, pdf_path, filename, t0, content_hash)
            )
        except Exception as e:
            await _update_doc_status(
                doc_id, "crashed",
                error_message=_exc_message(e),
                processing_time=round(time.monotonic() - t0, 2),
            )


async def _stage1_worker() -> None:
    """Drains _queue and fires a bounded concurrent task per document."""
    while True:
        item = await _queue.get()
        asyncio.create_task(_stage1_process(*item))
        _queue.task_done()


def _exc_message(exc: BaseException) -> str:
    """Some exceptions (notably httpx's timeout/connection classes)
    stringify to an empty string, which would otherwise silently produce
    a blank, unhelpful error_message in the UI."""
    msg = str(exc)
    return msg if msg else type(exc).__name__


# ── Stage 2: VLM single worker ────────────────────────────────────────────────

async def _stage2_worker() -> None:
    """Runs VLM on one page at a time (120 s timeout per page), across all
    pages of one document, then reconciles the per-page results into a
    single record before handing off to Stage 3."""
    while True:
        prepared = await _preprocessed_queue.get()
        try:
            page_fields: list[dict] = []
            page_errors: list[str]  = []

            for page_path, page_id in zip(prepared.dest_paths, prepared.page_ids):
                page_t0 = time.monotonic()
                await _update_page_status(page_id, "processing")
                try:
                    raw = await asyncio.wait_for(_run_vlm(page_path), timeout=VLM_PAGE_TIMEOUT)
                    if raw.get("_parse_error") or raw.get("_error"):
                        err = raw.get("_raw", "VLM parse error")[:500]
                        page_errors.append(err)
                        await _update_page_status(
                            page_id, "failed", error_message=err,
                            processing_time=round(time.monotonic() - page_t0, 2),
                        )
                    else:
                        page_fields.append(raw)
                        await _update_page_status(
                            page_id, "completed", fields=raw,
                            processing_time=round(time.monotonic() - page_t0, 2),
                        )
                except asyncio.TimeoutError:
                    err = f"VLM timeout ({VLM_PAGE_TIMEOUT:.0f} s)"
                    page_errors.append(err)
                    await _update_page_status(
                        page_id, "failed", error_message=err,
                        processing_time=round(time.monotonic() - page_t0, 2),
                    )
                except Exception as exc:
                    err = _exc_message(exc)[:500]
                    page_errors.append(err)
                    await _update_page_status(
                        page_id, "failed", error_message=err,
                        processing_time=round(time.monotonic() - page_t0, 2),
                    )

            if page_fields:
                fields = _reconcile_pages(page_fields)
                error  = None
            else:
                fields = {}
                error  = "; ".join(page_errors)[:500] if page_errors else "VLM produced no results"

            await _vlm_queue.put(
                _VlmResult(
                    doc_id       = prepared.doc_id,
                    batch_id     = prepared.batch_id,
                    dest_paths   = prepared.dest_paths,
                    pdf_path     = prepared.pdf_path,
                    filename     = prepared.filename,
                    t0           = prepared.t0,
                    fields       = fields,
                    error        = error,
                    content_hash = prepared.content_hash,
                )
            )
        finally:
            _preprocessed_queue.task_done()


# ── Stage 3: Post-processing pool ─────────────────────────────────────────────

async def _stage3_process(result: _VlmResult) -> None:
    """Run schema inference and DB writes under the semaphore; update status."""
    async with _postprocess_sem:
        primary_image    = str(result.dest_paths[0])
        page_image_paths = [str(p) for p in result.dest_paths]
        source_pdf_path  = str(result.pdf_path) if result.pdf_path else None

        if result.error:
            await _update_doc_status(
                result.doc_id, "crashed",
                error_message=result.error,
                image_path=primary_image,
                processing_time=round(time.monotonic() - result.t0, 2),
            )
            await _store_extraction(
                result.batch_id, primary_image, {}, "unknown", "manual_entry",
                content_hash=result.content_hash,
                source_pdf_path=source_pdf_path,
                page_image_paths=page_image_paths,
                batch_document_id=result.doc_id,
            )
            return

        confidence    = result.fields.get("extraction_confidence", "low")
        document_type = result.fields.get("document_type", "document")
        review_status = "auto_approved" if confidence == "high" else "review_required"
        final_status  = "completed"     if confidence == "high" else "review_required"

        try:
            await _store_extraction(
                result.batch_id, primary_image,
                result.fields, confidence, review_status,
                content_hash=result.content_hash,
                source_pdf_path=source_pdf_path,
                page_image_paths=page_image_paths,
                batch_document_id=result.doc_id,
            )
        except Exception as e:
            await _update_doc_status(
                result.doc_id, "crashed",
                error_message=f"DB store error: {_exc_message(e)}",
                image_path=primary_image,
                processing_time=round(time.monotonic() - result.t0, 2),
            )
            return

        await _update_doc_status(
            result.doc_id, final_status,
            document_type=document_type,
            confidence=confidence,
            image_path=primary_image,
            processing_time=round(time.monotonic() - result.t0, 2),
        )


async def _stage3_worker() -> None:
    """Drains _vlm_queue and fires a bounded concurrent task per result."""
    while True:
        result = await _vlm_queue.get()
        asyncio.create_task(_stage3_process(result))
        _vlm_queue.task_done()


# ── Batch creation helpers ────────────────────────────────────────────────────

async def _create_batch(source_type: str) -> str:
    async with _session()() as sess:
        result = await sess.execute(
            text("INSERT INTO batches (source_type) VALUES (:s) RETURNING id"),
            {"s": source_type},
        )
        batch_id = str(result.scalar())
        await sess.commit()
    return batch_id


async def _register_and_enqueue(
    batch_id: str, src_path: Path, filename: str
) -> None:
    content_hash = await asyncio.to_thread(_compute_hash, src_path)
    duplicate    = await _find_duplicate(content_hash)

    if duplicate:
        async with _session()() as sess:
            await sess.execute(
                text(
                    "INSERT INTO batch_documents (batch_id, filename, status)"
                    " VALUES (:b, :f, 'duplicate')"
                ),
                {"b": batch_id, "f": filename},
            )
            await sess.commit()
        return

    async with _session()() as sess:
        result = await sess.execute(
            text(
                "INSERT INTO batch_documents (batch_id, filename, source_path)"
                " VALUES (:b, :f, :sp) RETURNING id"
            ),
            {"b": batch_id, "f": filename, "sp": str(src_path)},
        )
        doc_id = str(result.scalar())
        await sess.commit()
    await _queue.put((doc_id, batch_id, src_path, filename, content_hash))


async def _retry_document(table_name: str, doc_id: str) -> str:
    """Re-run VLM extraction for a crashed (manual_entry) document, reusing
    its already-preprocessed page image(s) instead of re-uploading.

    Returns the new batch_id, pollable the normal way via
    GET /api/ingest/status/{batch_id}.
    """
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                f'SELECT source_pdf_path, page_image_paths, source_image_path,'
                f' content_hash, review_status FROM "{table_name}"'
                f' WHERE id = CAST(:id AS uuid)'
            ),
            {"id": doc_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if record["review_status"] != "manual_entry":
        raise HTTPException(
            status_code=422,
            detail="Only crashed documents (manual_entry status) can be retried.",
        )

    if record["page_image_paths"]:
        page_paths = [Path(p) for p in record["page_image_paths"]]
    elif record["source_image_path"]:
        page_paths = [Path(record["source_image_path"])]
    else:
        raise HTTPException(status_code=422, detail="No stored images to retry from.")

    missing = [str(p) for p in page_paths if not p.exists()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Stored image(s) no longer on disk: {', '.join(missing)}."
                   " Re-upload the original file instead.",
        )

    pdf_path     = Path(record["source_pdf_path"]) if record["source_pdf_path"] else None
    content_hash = record["content_hash"]

    # Remove the old crashed stub before reprocessing, so a successful
    # retry doesn't leave a duplicate empty row behind.
    async with _engine().begin() as conn:
        await conn.execute(
            text(f'DELETE FROM "{table_name}" WHERE id = CAST(:id AS uuid)'),
            {"id": doc_id},
        )

    batch_id = await _create_batch("retry")
    filename = pdf_path.name if pdf_path else page_paths[0].name
    source_path = pdf_path if pdf_path else page_paths[0]
    async with _session()() as sess:
        result = await sess.execute(
            text(
                "INSERT INTO batch_documents (batch_id, filename, status, source_path)"
                " VALUES (:b, :f, 'processing', :sp) RETURNING id"
            ),
            {"b": batch_id, "f": filename, "sp": str(source_path)},
        )
        new_doc_id = str(result.scalar())
        await sess.commit()

    # Preprocessing (Stage 1) already happened before the original crash —
    # skip straight to VLM extraction (Stage 2) with the stored images.
    page_ids = await _create_page_rows(new_doc_id, page_paths)
    await _preprocessed_queue.put(
        _PreparedDoc(new_doc_id, batch_id, page_paths, page_ids, pdf_path, filename,
                     time.monotonic(), content_hash)
    )
    return batch_id


async def _merge_page_into_document(batch_document_id: str) -> None:
    """Re-reconcile all completed/manually-entered pages for a document and
    update its already-stored record in place. Used after a per-page retry
    or manual entry, so the change is reflected even when the document was
    already finalized by Stage 3 before this page succeeded.

    A no-op if the document hasn't been finalized yet (or its document_type
    changed since — a known simplification: the row is looked up by its
    *current* inferred table, not moved if the type changes between
    reconciliations).
    """
    async with _engine().connect() as conn:
        pages_result = await conn.execute(
            text(
                "SELECT fields FROM batch_document_pages"
                " WHERE batch_document_id = CAST(:bd AS uuid)"
                "   AND status IN ('completed', 'manual')"
                " ORDER BY page_number"
            ),
            {"bd": batch_document_id},
        )
        page_fields = [row[0] for row in pages_result if row[0]]

    if not page_fields:
        return

    merged     = _reconcile_pages(page_fields)
    doc_type   = merged.get("document_type") or "document"
    table_name = _sanitize_identifier(doc_type)

    async with _engine().begin() as conn:
        table_exists = await conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = :t)"
            ),
            {"t": table_name},
        )
        if not table_exists.scalar():
            return

        row_result = await conn.execute(
            text(f'SELECT id FROM "{table_name}" WHERE batch_document_id = CAST(:bd AS uuid)'),
            {"bd": batch_document_id},
        )
        row = row_result.one_or_none()
        if row is None:
            return

        await _ensure_table(conn, table_name, merged)

        set_parts = ['"confidence" = :confidence']
        params: dict = {
            "id":         str(row[0]),
            "confidence": merged.get("extraction_confidence", "low"),
        }
        for k, v in merged.items():
            if k.startswith("_") or k == "extraction_confidence":
                continue
            col = _sanitize_identifier(k)
            set_parts.append(f'"{col}" = :{col}')
            params[col] = _serialize(v)

        await conn.execute(
            text(f'UPDATE "{table_name}" SET {", ".join(set_parts)} WHERE id = CAST(:id AS uuid)'),
            params,
        )

    await invalidate_cache("review")


async def _reload_single_page(page_id: str) -> None:
    """Validate the page can be reloaded from its original source, then
    hand the actual re-derivation + VLM call off to a background task
    (same reasoning as _retry_single_page — this can take a while)."""
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT p.source_page_number, d.source_path"
                " FROM batch_document_pages p"
                " JOIN batch_documents d ON d.id = p.batch_document_id"
                " WHERE p.id = CAST(:id AS uuid)"
            ),
            {"id": page_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Page not found")
    if record["source_page_number"] is None or not record["source_path"]:
        raise HTTPException(
            status_code=422,
            detail="The original source file wasn't recorded for this document "
                   "(it was ingested before this feature existed). Use Retry "
                   "instead, or re-upload the file.",
        )

    source_path = Path(record["source_path"])
    if not source_path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Original source file no longer on disk: {source_path}",
        )

    asyncio.create_task(_run_page_reload(page_id))


async def _run_page_reload(page_id: str) -> None:
    """Background counterpart to _reload_single_page — re-derives the page
    from its original source document (re-running PDF-page extraction and
    preprocessing, including the current column-split detection) rather
    than reusing the already-preprocessed stored image, then replaces the
    existing row(s) for that source page and merges the result in."""
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT p.batch_document_id, p.source_page_number,"
                "       d.source_path, d.filename, d.batch_id"
                " FROM batch_document_pages p"
                " JOIN batch_documents d ON d.id = p.batch_document_id"
                " WHERE p.id = CAST(:id AS uuid)"
            ),
            {"id": page_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        return  # page was deleted between validation and now

    batch_document_id  = record["batch_document_id"]
    source_page_number = record["source_page_number"]
    source_path         = Path(record["source_path"])
    batch_id             = str(record["batch_id"])
    filename              = record["filename"]

    try:
        if source_path.suffix.lower() == ".pdf":
            page_src = await asyncio.to_thread(
                _pdf_page_to_png, source_path, source_page_number
            )
        else:
            page_src = source_path

        processed = await asyncio.to_thread(_preprocess, page_src)
    except Exception:
        # Leave the existing row(s) untouched if re-derivation itself
        # fails (e.g. a transient PDF-rendering error) — nothing to
        # merge, and we haven't touched the DB yet.
        return

    # Every existing row for this original page — one if it wasn't split
    # before, two if it was — is being replaced.
    async with _engine().connect() as conn:
        existing_rows = await conn.execute(
            text(
                "SELECT id, image_path FROM batch_document_pages"
                " WHERE batch_document_id = CAST(:bd AS uuid)"
                "   AND source_page_number = :sn"
            ),
            {"bd": batch_document_id, "sn": source_page_number},
        )
        existing = [(str(r[0]), Path(r[1])) for r in existing_rows]

    async with _engine().begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM batch_document_pages"
                " WHERE batch_document_id = CAST(:bd AS uuid)"
                "   AND source_page_number = :sn"
            ),
            {"bd": batch_document_id, "sn": source_page_number},
        )

    for _old_id, old_path in existing:
        old_path.unlink(missing_ok=True)

    dest_dir = IMAGES_DIR / batch_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    page_stem = f"{stem}_page{source_page_number:03d}"
    new_paths: list[Path] = []
    for j, img in enumerate(processed):
        suffix = chr(ord("a") + j) if len(processed) > 1 else ""
        dest_path = dest_dir / f"{page_stem}{suffix}.png"
        await asyncio.to_thread(img.save, str(dest_path), "PNG")
        new_paths.append(dest_path)

    new_page_ids = await _create_page_rows(
        batch_document_id, new_paths, [source_page_number] * len(new_paths)
    )

    for pid, path in zip(new_page_ids, new_paths):
        await _update_page_status(pid, "processing")
        await _process_one_page(pid, path)

    await _merge_page_into_document(batch_document_id)


async def _retry_single_page(page_id: str) -> None:
    """Validate the page and mark it 'processing', then hand the actual
    VLM call off to a background task. A VLM call can take up to
    VLM_PAGE_TIMEOUT (240s+) — awaiting it directly in the request handler
    would block the HTTP response past nginx's own proxy timeout, exactly
    the failure mode the rest of this pipeline avoids by being async
    (upload returns a batch_id immediately; status is polled). The
    frontend's existing per-page polling picks up the eventual result."""
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT batch_document_id, image_path"
                " FROM batch_document_pages WHERE id = CAST(:id AS uuid)"
            ),
            {"id": page_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Page not found")

    image_path = Path(record["image_path"])
    if not image_path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Stored page image no longer on disk: {image_path}",
        )

    await _update_page_status(page_id, "processing")
    asyncio.create_task(
        _run_page_retry(page_id, record["batch_document_id"], image_path)
    )


async def _process_one_page(page_id: str, image_path: Path) -> bool:
    """Run VLM extraction for one page and update its row. Returns True on
    success. Does not merge into the parent document — callers merge once
    after processing one or more pages, so a bulk resume doesn't re-run
    the (cheap but non-trivial) reconciliation after every single page."""
    t0 = time.monotonic()
    try:
        raw = await asyncio.wait_for(_run_vlm(image_path), timeout=VLM_PAGE_TIMEOUT)
    except asyncio.TimeoutError:
        await _update_page_status(
            page_id, "failed",
            error_message=f"VLM timeout ({VLM_PAGE_TIMEOUT:.0f} s)",
            processing_time=round(time.monotonic() - t0, 2),
        )
        return False
    except Exception as exc:
        await _update_page_status(
            page_id, "failed", error_message=_exc_message(exc)[:500],
            processing_time=round(time.monotonic() - t0, 2),
        )
        return False

    if raw.get("_parse_error") or raw.get("_error"):
        await _update_page_status(
            page_id, "failed",
            error_message=raw.get("_raw", "VLM parse error")[:500],
            processing_time=round(time.monotonic() - t0, 2),
        )
        return False

    await _update_page_status(
        page_id, "completed", fields=raw,
        processing_time=round(time.monotonic() - t0, 2),
    )
    return True


async def _run_page_retry(page_id: str, batch_document_id: str, image_path: Path) -> None:
    """Background counterpart to _retry_single_page — runs the VLM call
    and merges the result, without blocking the HTTP request that
    triggered it."""
    await _process_one_page(page_id, image_path)
    await _merge_page_into_document(batch_document_id)


async def _resume_document_pages(batch_document_id: str) -> int:
    """Queue every pending/failed/stuck-processing page of a document for
    (re)processing — used to pick a document back up after an interruption
    (e.g. an API restart mid-run) without re-attempting pages that already
    succeeded. Returns the number of pages queued, 0 if there's nothing to
    resume."""
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT id, image_path FROM batch_document_pages"
                " WHERE batch_document_id = CAST(:bd AS uuid)"
                "   AND status IN ('pending', 'failed', 'processing')"
                " ORDER BY page_number"
            ),
            {"bd": batch_document_id},
        )
        pages = [(str(r[0]), Path(r[1])) for r in rows]

    if not pages:
        return 0

    # Reset any stale 'processing' rows (left over from an interrupted run)
    # to 'pending' up front, so the resume loop below is the only thing
    # marking a page 'processing' at any given moment — otherwise a
    # stuck-but-not-yet-reached page would misleadingly still show as
    # "processing" alongside the one actually being worked on.
    async with _engine().begin() as conn:
        await conn.execute(
            text(
                "UPDATE batch_document_pages SET status = 'pending'"
                " WHERE batch_document_id = CAST(:bd AS uuid) AND status = 'processing'"
            ),
            {"bd": batch_document_id},
        )

    asyncio.create_task(_run_resume(batch_document_id, pages))
    return len(pages)


async def _run_resume(batch_document_id: str, pages: list[tuple[str, Path]]) -> None:
    """Background: process pages one at a time (not concurrently) so a
    resume doesn't overwhelm Ollama the way N simultaneous retries would."""
    for page_id, image_path in pages:
        await _update_page_status(page_id, "processing")
        if not image_path.exists():
            await _update_page_status(
                page_id, "failed",
                error_message=f"Stored page image no longer on disk: {image_path}",
            )
            continue
        await _process_one_page(page_id, image_path)
    await _merge_page_into_document(batch_document_id)


async def _manual_enter_page(page_id: str, fields: dict) -> None:
    """Store a human-entered result for one page (bypassing the VLM
    entirely) and merge it back into the parent document."""
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT batch_document_id"
                " FROM batch_document_pages WHERE id = CAST(:id AS uuid)"
            ),
            {"id": page_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Page not found")

    await _update_page_status(page_id, "manual", fields=fields, processing_time=0.0)
    await _merge_page_into_document(record["batch_document_id"])


async def _skip_page(page_id: str) -> None:
    """Mark a page as not relevant (cover sheet, blank, out-of-scope
    layout, ...) and exclude it from the document's reconciled result. If
    the page had previously contributed content (e.g. it was completed
    then reconsidered), re-merging removes that contribution."""
    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT batch_document_id"
                " FROM batch_document_pages WHERE id = CAST(:id AS uuid)"
            ),
            {"id": page_id},
        )
        record = row.mappings().one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Page not found")

    await _update_page_status(page_id, "skipped", error_message=None, processing_time=0.0)
    await _merge_page_into_document(record["batch_document_id"])


# ── Google Drive helper ───────────────────────────────────────────────────────

async def _download_google_drive_folder(folder_id: str, dest_dir: Path) -> list[Path]:
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_file:
        raise HTTPException(
            status_code=501,
            detail="Google Drive not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE env var.",
        )
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="google-api-python-client not installed.",
        )

    creds   = service_account.Credentials.from_service_account_file(
        sa_file, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    mime_filter = " or ".join(
        f"mimeType='{m}'"
        for m in ("image/jpeg", "image/png", "application/pdf")
    )
    items = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and ({mime_filter})",
            fields="files(id, name)",
            pageSize=200,
        )
        .execute()
        .get("files", [])
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for item in items:
        dest = dest_dir / item["name"]
        req  = service.files().get_media(fileId=item["id"])
        with open(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
        downloaded.append(dest)

    return downloaded


# ── API router ────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    current_user: str = Depends(get_current_user),
):
    """Accept one or more image/PDF files, return a batch_id immediately."""
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXTS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type '{ext}' for {f.filename}."
                       f" Allowed: {', '.join(sorted(ALLOWED_EXTS))}",
            )

    batch_id  = await _create_batch("upload")
    batch_dir = UPLOADS_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        filename = f.filename or f"file_{uuid.uuid4().hex}"
        dest     = batch_dir / filename
        content  = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{filename} exceeds the maximum upload size "
                       f"({MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
            )
        dest.write_bytes(content)
        await _register_and_enqueue(batch_id, dest, filename)

    return {"batch_id": batch_id}


class PathRequest(BaseModel):
    path: Optional[str] = None
    google_drive_folder_id: Optional[str] = None


@router.post("/path")
@limiter.limit("5/minute")
async def ingest_from_path(
    request: Request,
    body: PathRequest,
    current_user: str = Depends(require_admin),
):
    """Ingest from a server-side directory path or a Google Drive folder.

    Requires admin role. Server-side paths are restricted to INGEST_ROOT to
    prevent an admin account from reading arbitrary filesystem locations.
    """
    if not body.path and not body.google_drive_folder_id:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'path' or 'google_drive_folder_id'.",
        )

    batch_id = await _create_batch(
        "google_drive" if body.google_drive_folder_id else "server_path"
    )

    if body.google_drive_folder_id:
        dest_dir = DOCS_DIR  # as per spec: copy to anonymized_docs/
        files = await asyncio.to_thread(
            _download_google_drive_folder_sync,
            body.google_drive_folder_id,
            dest_dir,
        )
    else:
        src_dir = Path(body.path).resolve()
        if not src_dir.is_relative_to(INGEST_ROOT):
            raise HTTPException(
                status_code=403,
                detail=f"Path is outside the allowed ingest directory ({INGEST_ROOT}).",
            )
        if not src_dir.exists() or not src_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Path not found: {body.path}")
        files = [
            p for p in src_dir.iterdir()
            if p.suffix.lower() in ALLOWED_EXTS
        ]

    if not files:
        raise HTTPException(status_code=422, detail="No supported files found.")

    for p in files:
        await _register_and_enqueue(batch_id, p, p.name)

    return {"batch_id": batch_id}


def _download_google_drive_folder_sync(folder_id: str, dest_dir: Path) -> list[Path]:
    """Sync wrapper so it can be called via asyncio.to_thread."""
    sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not sa_file:
        raise RuntimeError(
            "Google Drive not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        raise RuntimeError("google-api-python-client not installed.")

    creds   = service_account.Credentials.from_service_account_file(
        sa_file, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    mime_filter = " or ".join(
        f"mimeType='{m}'"
        for m in ("image/jpeg", "image/png", "application/pdf")
    )
    items = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and ({mime_filter})",
            fields="files(id, name)",
            pageSize=200,
        )
        .execute()
        .get("files", [])
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for item in items:
        dest = dest_dir / item["name"]
        req  = service.files().get_media(fileId=item["id"])
        with open(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
        downloaded.append(dest)

    return downloaded


@router.get("/batches")
async def list_batches(
    limit:        int = 20,
    current_user: str = Depends(get_current_user),
):
    """
    Return the most recent ingestion batches with aggregate progress, so
    an in-progress or completed batch can be found again after a page
    refresh or from a different session — the frontend previously only
    tracked the current batch_id in memory, losing it on reload.
    """
    limit = max(1, min(limit, 100))
    async with _session()() as sess:
        result = await sess.execute(
            text("""
                SELECT
                    b.id::text AS batch_id,
                    b.source_type,
                    b.created_at,
                    COUNT(bd.id)                                                    AS total,
                    COUNT(*) FILTER (WHERE bd.status = 'completed')                 AS completed,
                    COUNT(*) FILTER (WHERE bd.status = 'crashed')                   AS crashed,
                    COUNT(*) FILTER (WHERE bd.status = 'review_required')           AS review_required,
                    COUNT(*) FILTER (WHERE bd.status = 'out_of_scope')              AS out_of_scope,
                    COUNT(*) FILTER (WHERE bd.status IN ('pending', 'processing'))  AS pending,
                    COUNT(*) FILTER (WHERE bd.status = 'duplicate')                 AS duplicates_skipped
                FROM batches b
                LEFT JOIN batch_documents bd ON bd.batch_id = b.id
                GROUP BY b.id, b.source_type, b.created_at
                ORDER BY b.created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        rows = result.mappings().all()

    return [
        {
            "batch_id":           r["batch_id"],
            "source_type":        r["source_type"],
            "created_at":         r["created_at"].isoformat(),
            "total":              r["total"],
            "completed":          r["completed"],
            "crashed":            r["crashed"],
            "review_required":    r["review_required"],
            "out_of_scope":       r["out_of_scope"],
            "pending":            r["pending"],
            "duplicates_skipped": r["duplicates_skipped"],
        }
        for r in rows
    ]


@router.get("/status/{batch_id}")
async def get_batch_status(
    batch_id: str,
    current_user: str = Depends(get_current_user),
):
    """Return overall batch progress and per-document status."""
    async with _session()() as sess:
        docs_result = await sess.execute(
            text(
                "SELECT id, filename, status, document_type, confidence,"
                " processing_time, error_message, image_path"
                " FROM batch_documents WHERE batch_id = :b ORDER BY ingested_at"
            ),
            {"b": batch_id},
        )
        rows = docs_result.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    documents = [
        {
            "id":              str(r[0]),
            "filename":        r[1],
            "status":          r[2],
            "document_type":   r[3],
            "confidence":      r[4],
            "processing_time": r[5],
            "error_message":   r[6],
            "image_path":      r[7],
        }
        for r in rows
    ]

    def _count(s: str) -> int:
        return sum(1 for d in documents if d["status"] == s)

    return {
        "batch_id":           batch_id,
        "total":              len(documents),
        "completed":          _count("completed"),
        "crashed":            _count("crashed"),
        "review_required":    _count("review_required"),
        "out_of_scope":       _count("out_of_scope"),
        "pending":            _count("pending") + _count("processing"),
        "duplicates_skipped": _count("duplicate"),
        "documents":          documents,
    }


@router.get("/pages/{batch_document_id}")
async def list_pages(
    batch_document_id: str,
    current_user:       str = Depends(get_current_user),
):
    """Per-page status for one document — powers the page-level progress
    bar and failed-pages list for multi-page documents."""
    async with _session()() as sess:
        result = await sess.execute(
            text(
                "SELECT id, page_number, image_path, status, error_message,"
                " processing_time, fields, updated_at"
                " FROM batch_document_pages WHERE batch_document_id = CAST(:bd AS uuid)"
                " ORDER BY page_number"
            ),
            {"bd": batch_document_id},
        )
        rows = result.mappings().all()

    if not rows:
        raise HTTPException(status_code=404, detail="No pages found for this document")

    pages = [
        {
            "id":              str(r["id"]),
            "page_number":     r["page_number"],
            "image_path":      r["image_path"],
            "status":          r["status"],
            "error_message":   r["error_message"],
            "processing_time": r["processing_time"],
            "fields":          r["fields"],
            "updated_at":      r["updated_at"].isoformat(),
        }
        for r in rows
    ]

    def _count(s: str) -> int:
        return sum(1 for p in pages if p["status"] == s)

    return {
        "batch_document_id": batch_document_id,
        "total":             len(pages),
        "pending":           _count("pending"),
        "processing":        _count("processing"),
        "completed":         _count("completed"),
        "failed":            _count("failed"),
        "manual":            _count("manual"),
        "skipped":           _count("skipped"),
        "pages":             pages,
    }


class ManualPageEntry(BaseModel):
    fields: dict


@router.post("/pages/{page_id}/retry")
@limiter.limit("30/minute")
async def retry_page(
    request:      Request,
    page_id:      str,
    current_user: str = Depends(get_current_user),
):
    """Kick off a re-run of VLM extraction for one failed page in the
    background and return immediately; merges into the parent document
    (even if already finalized) once it completes. Poll
    GET /api/ingest/pages/{batch_document_id} for the result."""
    await _retry_single_page(page_id)
    return {"ok": True, "status": "processing"}


@router.post("/pages/{page_id}/reload")
@limiter.limit("20/minute")
async def reload_page(
    request:      Request,
    page_id:      str,
    current_user: str = Depends(get_current_user),
):
    """Re-derive this page from its original source document from
    scratch — re-running PDF-page extraction and preprocessing (including
    the current column-split detection) — instead of reusing the
    already-preprocessed stored image. Useful when a page was processed
    under since-fixed preprocessing logic. Runs in the background; poll
    GET /api/ingest/pages/{batch_document_id} for the result. Requires
    the document to have been ingested after source-tracking was added —
    older documents don't have a recorded source path."""
    await _reload_single_page(page_id)
    return {"ok": True, "status": "processing"}


@router.post("/pages/{page_id}/manual")
@limiter.limit("30/minute")
async def manual_enter_page(
    request:      Request,
    page_id:      str,
    body:         ManualPageEntry,
    current_user: str = Depends(get_current_user),
):
    """Store a human-entered result for one page (bypassing the VLM) and
    merge it into the parent document."""
    await _manual_enter_page(page_id, body.fields)
    return {"ok": True}


@router.post("/pages/{page_id}/skip")
@limiter.limit("60/minute")
async def skip_page(
    request:      Request,
    page_id:      str,
    current_user: str = Depends(get_current_user),
):
    """Mark a page as not relevant (cover sheet, blank, out-of-scope
    layout, ...) so it's excluded from the document's reconciled result."""
    await _skip_page(page_id)
    return {"ok": True}


@router.post("/pages/resume/{batch_document_id}")
@limiter.limit("10/minute")
async def resume_document(
    request:            Request,
    batch_document_id:  str,
    current_user:       str = Depends(get_current_user),
):
    """Resume every pending/failed page of a document in the background —
    e.g. after an interrupted run (API restart mid-processing). Pages are
    processed one at a time; poll GET /api/ingest/pages/{batch_document_id}
    for progress. Already-completed pages are left untouched."""
    count = await _resume_document_pages(batch_document_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="No pending or failed pages to resume")
    return {"ok": True, "queued": count}


@router.delete("/documents/{batch_document_id}")
@limiter.limit("30/minute")
async def delete_ingested_document(
    request:            Request,
    batch_document_id:  str,
    current_user:       str = Depends(require_admin),
):
    """Delete the extracted document for one upload — found via its
    batch_document_id, regardless of which per-type table it's in — so
    the same file can be re-uploaded without tripping the duplicate
    check. Admin only, since this is irreversible. Leaves the batch/page
    history in place; only removes the stored extraction."""
    deleted = await _delete_document_by_batch_document_id(batch_document_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="No extracted document found for this upload — it may not have "
                   "finished processing yet, or was already deleted.",
        )
    return {"ok": True}
