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
MIN_CHAR_COUNT = 50

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
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
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
    dest_path:    Path
    filename:     str
    t0:           float
    content_hash: str


class _VlmResult(NamedTuple):
    doc_id:       str
    batch_id:     str
    dest_path:    Path
    filename:     str
    t0:           float
    fields:       dict         # VLM result fields, or {} on any error
    error:        Optional[str]  # None on success; message on crash/timeout/parse error
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


# ── VLM prompt (identical to vlm_ollama_test.py) ─────────────────────────────

_VLM_PROMPT = """This is a Chadian government administrative document.
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


def _preprocess(src: Path, dest: Path) -> None:
    """Full preprocessing pipeline — runs in a thread executor."""
    img = Image.open(src).convert("RGB")
    img = _correct_orientation(img)
    img = _remove_flag_stripes(img)
    img = _resize_max(img, VLM_MAX_SIZE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), "PNG")


# ── Document classifier ───────────────────────────────────────────────────────

def _has_enough_text(img_path: Path) -> bool:
    """Returns True when Tesseract finds ≥ MIN_CHAR_COUNT chars on a thumbnail."""
    try:
        img = Image.open(img_path)
        img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        text = pytesseract.image_to_string(img, lang="fra+ara", timeout=30)
        return len(text.strip()) >= MIN_CHAR_COUNT
    except Exception:
        return False


# ── VLM extraction (async, Ollama REST) ───────────────────────────────────────

async def _run_vlm(img_path: Path) -> dict:
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    async with httpx.AsyncClient(timeout=115.0) as client:
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
                text(f'SELECT id FROM "{table_name}" WHERE content_hash = :h LIMIT 1'),
                {"h": content_hash},
            )
            match = row.one_or_none()
            if match:
                return {"table_name": table_name, "id": str(match[0])}
    return None


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
            "batch_id         UUID",
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

    if "content_hash" not in existing:
        await conn.execute(text(
            f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS content_hash TEXT'
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


# ── Stage 1: Preprocessing pool ───────────────────────────────────────────────

async def _stage1_process(
    doc_id: str, batch_id: str, src_path: Path, filename: str, content_hash: str
) -> None:
    """Preprocess one document under the semaphore; push to _preprocessed_queue."""
    async with _preprocess_sem:
        t0 = time.monotonic()
        await _update_doc_status(doc_id, "processing")
        try:
            if src_path.suffix.lower() == ".pdf":
                pages = await asyncio.to_thread(_pdf_to_png, src_path)
                src_path = pages[0] if pages else src_path

            dest_path = IMAGES_DIR / batch_id / (Path(filename).stem + ".png")
            await asyncio.to_thread(_preprocess, src_path, dest_path)

            has_text = await asyncio.to_thread(_has_enough_text, dest_path)
            if not has_text:
                await _update_doc_status(
                    doc_id, "out_of_scope",
                    image_path=str(dest_path),
                    processing_time=round(time.monotonic() - t0, 2),
                )
                return

            await _preprocessed_queue.put(
                _PreparedDoc(doc_id, batch_id, dest_path, filename, t0, content_hash)
            )
        except Exception as e:
            await _update_doc_status(
                doc_id, "crashed",
                error_message=str(e),
                processing_time=round(time.monotonic() - t0, 2),
            )


async def _stage1_worker() -> None:
    """Drains _queue and fires a bounded concurrent task per document."""
    while True:
        item = await _queue.get()
        asyncio.create_task(_stage1_process(*item))
        _queue.task_done()


# ── Stage 2: VLM single worker ────────────────────────────────────────────────

async def _stage2_worker() -> None:
    """Runs VLM on one document at a time; applies 120 s timeout per document."""
    while True:
        prepared = await _preprocessed_queue.get()
        try:
            try:
                raw = await asyncio.wait_for(
                    _run_vlm(prepared.dest_path), timeout=120.0
                )
                if raw.get("_parse_error") or raw.get("_error"):
                    fields = {}
                    error  = raw.get("_raw", "VLM parse error")[:500]
                else:
                    fields = raw
                    error  = None
            except asyncio.TimeoutError:
                fields = {}
                error  = "VLM timeout (120 s)"
            except Exception as exc:
                fields = {}
                error  = str(exc)

            await _vlm_queue.put(
                _VlmResult(
                    doc_id       = prepared.doc_id,
                    batch_id     = prepared.batch_id,
                    dest_path    = prepared.dest_path,
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
        if result.error:
            await _update_doc_status(
                result.doc_id, "crashed",
                error_message=result.error,
                image_path=str(result.dest_path),
                processing_time=round(time.monotonic() - result.t0, 2),
            )
            await _store_extraction(
                result.batch_id, str(result.dest_path), {}, "unknown", "manual_entry",
                content_hash=result.content_hash,
            )
            return

        confidence    = result.fields.get("extraction_confidence", "low")
        document_type = result.fields.get("document_type", "document")
        review_status = "auto_approved" if confidence == "high" else "review_required"
        final_status  = "completed"     if confidence == "high" else "review_required"

        try:
            await _store_extraction(
                result.batch_id, str(result.dest_path),
                result.fields, confidence, review_status,
                content_hash=result.content_hash,
            )
        except Exception as e:
            await _update_doc_status(
                result.doc_id, "crashed",
                error_message=f"DB store error: {e}",
                image_path=str(result.dest_path),
                processing_time=round(time.monotonic() - result.t0, 2),
            )
            return

        await _update_doc_status(
            result.doc_id, final_status,
            document_type=document_type,
            confidence=confidence,
            image_path=str(result.dest_path),
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
                "INSERT INTO batch_documents (batch_id, filename)"
                " VALUES (:b, :f) RETURNING id"
            ),
            {"b": batch_id, "f": filename},
        )
        doc_id = str(result.scalar())
        await sess.commit()
    await _queue.put((doc_id, batch_id, src_path, filename, content_hash))


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
