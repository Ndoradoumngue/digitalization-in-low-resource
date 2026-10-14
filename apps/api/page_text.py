"""
Page text (GED Phase 4) - what is written on every page, searchable.

batch_document_pages.ocr_text holds Tesseract's transcription of the
preprocessed page image, manual_text the values someone typed for a page
entered by hand; ocr_tsv (a generated column, text search configuration
sdai_fr = french + unaccent) indexes both for GET /api/db/documents?q=.

ocr_text NULL means "not transcribed yet", '' means "transcribed, no text".
Transcription only ever runs on the local node (the pipeline lives there);
the cloud receives the text through sync. New pages are queued by
ingest_router._create_page_rows and transcribed by ingest_router's
_page_text_worker; pages that predate this (or whose queue entry was lost
to a restart) are filled by scripts/backfill_page_text.py.

No router imports here, so ingest_router and the backfill script can both
use it.
"""

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image
from sqlalchemy import text

log = logging.getLogger(__name__)

# Tesseract language packs to read pages with ("+"-separated, as Tesseract
# takes them). Every pack named here must be installed (Dockerfile).
OCR_LANGS = (os.getenv("OCR_LANGS") or "fra+eng").strip()
# A pathological page must not hold the transcription queue forever.
OCR_TIMEOUT_SECONDS = float(os.getenv("OCR_TIMEOUT_SECONDS") or 120.0)
# Off switch (e.g. a node too small to OCR next to the pipeline).
OCR_ENABLED = (os.getenv("OCR_ENABLED") or "true").strip().lower() not in (
    "0",
    "false",
    "no",
)

# French stemming without accents: "decret" and "décret" are the same
# word. CREATE TEXT SEARCH CONFIGURATION has no IF NOT EXISTS, hence the
# DO block (own statement - see ingest_router._init_db). unaccent is a
# trusted extension, so the database owner can create it.
SEARCH_CONFIG_DDL = (
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    r"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'sdai_fr') THEN
        CREATE TEXT SEARCH CONFIGURATION sdai_fr (COPY = french);
        ALTER TEXT SEARCH CONFIGURATION sdai_fr
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem;
    END IF;
END $$
""",
)

# The text search configuration every search query and FTS index uses.
TS_CONFIG = "sdai_fr"


def transcribe(image_path: Path) -> str:
    """Tesseract text of one page image. Blocking - call in a thread.
    Raises when the image or a language pack is missing, or on timeout."""
    with Image.open(image_path) as img:
        raw = pytesseract.image_to_string(
            img, lang=OCR_LANGS, timeout=OCR_TIMEOUT_SECONDS
        )
    return normalize(raw)


def normalize(raw: str) -> str:
    """Collapse runs of blank lines and trailing spaces; NUL bytes (which
    PostgreSQL TEXT refuses) dropped."""
    lines = [line.rstrip() for line in raw.replace("\x00", "").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def manual_text(fields: Mapping[str, Any] | None) -> str | None:
    """The searchable text of a page entered by hand: every value typed
    for it (nested lists/objects flattened), in field order."""
    if not fields:
        return None
    parts: list[str] = []

    def walk(v: Any) -> None:
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, Mapping):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        else:
            s = str(v).strip()
            if s:
                parts.append(s)

    walk(fields)
    return normalize("\n".join(parts)) or None


async def store(
    conn, page_id: str, ocr_text: str, *, only_if_missing: bool = False
) -> bool:
    """Save a page's transcription. False when the page is gone (deleted
    or reloaded while it was being read) - or, with only_if_missing, was
    transcribed meanwhile."""
    missing = " AND ocr_text IS NULL" if only_if_missing else ""
    result = await conn.execute(
        text(
            "UPDATE batch_document_pages SET ocr_text = :t, updated_at = now()"
            f" WHERE id = CAST(:id AS uuid){missing}"
        ),
        {"t": ocr_text, "id": page_id},
    )
    return result.rowcount > 0
