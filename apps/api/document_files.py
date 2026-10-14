"""
The file behind a filed document, for everything that hands one out (GED
Phase 5): the original download, share links and selective exports.

A document's files are its version rows (sdai_document_versions - version
1 is the ingested original, backfilled for every document); the document
row's current_version names the live one. Nothing here writes a file:
a watermarked download is built in memory, never stored.

  resolve(conn, table, doc_id, tenant_id, version)  -> DocumentFile | None
  check_servable(conn, f, locale, pending_status)    -> Path (raises 404/409)
  download_name(record_id, original_filename)       -> "<record id>_<name>"
  content_disposition(name, inline)                 -> RFC 6266 / 5987 header
  watermark_pdf(data, lines)                        -> bytes (pypdf overlay)
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from i18n import t
from sqlalchemy import text

# Stamp downloaded PDFs of confidential/secret documents with who downloaded
# them and when (the stored file is never changed - the copy handed out is).
WATERMARK_CONFIDENTIAL = (
    os.getenv("DOWNLOAD_WATERMARK_CONFIDENTIAL", "false").lower() == "true"
)
WATERMARK_LEVELS = ("confidential", "secret")


@dataclass(frozen=True)
class DocumentFile:
    table_name: str
    document_id: str
    record_id: str | None
    confidentiality: str | None
    version_no: int
    current_version: int | None
    file_path: str
    file_hash: str | None
    size: int | None
    mime: str | None
    original_filename: str | None

    @property
    def is_pdf(self) -> bool:
        return (
            self.mime or ""
        ) == "application/pdf" or self.file_path.lower().endswith(".pdf")

    def needs_watermark(self) -> bool:
        return (
            WATERMARK_CONFIDENTIAL
            and self.is_pdf
            and self.confidentiality in WATERMARK_LEVELS
        )


async def resolve(
    conn,
    table: str,
    cols: dict[str, str],
    doc_id: str,
    tenant_id: str,
    version_no: int | None = None,
) -> DocumentFile | None:
    """One version of a document (default: its current version), or None
    when the document or that version doesn't exist. `table` must already
    be a validated tenant table and the caller must have checked access."""
    record = "d.record_id::text" if "record_id" in cols else "NULL::text"
    row = (
        (
            await conn.execute(
                text(f"""
                    SELECT {record} AS record_id, d.confidentiality, d.current_version,
                           v.version_no, v.file_path, v.file_hash, v.size, v.mime,
                           v.original_filename
                    FROM "{table}" d
                    JOIN sdai_document_versions v
                      ON v.tenant_id = CAST(:tid AS uuid) AND v.table_name = :t
                     AND v.document_id = d.id
                     AND v.version_no = COALESCE(CAST(:n AS integer), d.current_version, 1)
                    WHERE d.id = CAST(:id AS uuid)
                """),
                {"tid": tenant_id, "t": table, "id": doc_id, "n": version_no},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return DocumentFile(table_name=table, document_id=doc_id, **dict(row))


def safe_path(file_path: str) -> Path | None:
    """The resolved path when it lies inside one of the servable roots
    (documents_router._SAFE_FILE_ROOTS), else None."""
    import documents_router

    path = Path(file_path).resolve()
    if any(path.is_relative_to(root) for root in documents_router._SAFE_FILE_ROOTS):
        return path
    return None


async def check_servable(
    conn, f: DocumentFile, locale: str, *, pending_status: int = 409
) -> Path:
    """The file's path, ready to stream - 404 when it's outside the
    servable roots or missing, `pending_status` (with X-Sync-Pending: 1)
    when local/cloud sync has not brought it to this node yet."""
    from sync import files as sync_files

    path = safe_path(f.file_path)
    if path is None:
        raise HTTPException(status_code=404, detail=t("common.file_not_found", locale))
    if not path.is_file():
        if await sync_files.file_pending(conn, f.file_path):
            raise HTTPException(
                status_code=pending_status,
                detail=t("sync.file_pending", locale),
                headers={"X-Sync-Pending": "1"},
            )
        raise HTTPException(status_code=404, detail=t("common.file_not_found", locale))
    return path


_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


def download_name(
    record_id: str | None, original_filename: str | None, fallback: str
) -> str:
    """'<record id>_<original name>' - the citable id first so a folder of
    downloads sorts and reads like the register. Path separators and
    control characters never survive."""
    name = Path(original_filename or "").name or fallback
    name = _UNSAFE_NAME.sub("_", name).strip(" .") or fallback
    if record_id:
        name = f"{_UNSAFE_NAME.sub('_', record_id)}_{name}"
    return name[:200]


def content_disposition(filename: str, inline: bool = False) -> str:
    """RFC 6266 header with an ASCII fallback and the exact UTF-8 name as
    filename* (RFC 5987) - accented names survive in every browser."""
    ascii_name = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_name = re.sub(r'["\\]', "_", ascii_name).strip() or "file"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ── PDF watermark ─────────────────────────────────────────────────────────────


def _pdf_string(s: str) -> bytes:
    """A PDF literal string in WinAnsi (what the standard Helvetica font
    reads), characters outside it replaced."""
    raw = s.encode("cp1252", "replace")
    return (
        b"("
        + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        + b")"
    )


def watermark_pdf(data: bytes, diagonal: str, footer: str) -> bytes:
    """Every page of the PDF with a translucent diagonal `diagonal` and a
    small `footer` line - drawn over the content, so it shows on scans.
    Raises pypdf's errors on an unreadable PDF (callers fall back to
    refusing the download, never to handing out an unmarked copy)."""
    from pypdf import PageObject, PdfReader, PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
    )

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    gstate = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/ExtGState"),
            NameObject("/ca"): FloatObject(0.22),
        }
    )
    for page in reader.pages:
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        x0, y0 = float(box.left), float(box.bottom)
        size = max(12.0, min(w, h) / 14)
        angle = math.atan2(h, w)
        c, s = math.cos(angle), math.sin(angle)
        # Centre the diagonal: Helvetica averages ~0.5 em per character.
        half = len(diagonal) * size * 0.28
        cx, cy = x0 + w / 2 - c * half, y0 + h / 2 - s * half
        ops = (
            b"q /SdaiWmGs gs 0.6 0 0 rg BT /SdaiWmF %.2f Tf %.4f %.4f %.4f %.4f %.2f %.2f Tm %s Tj ET Q\n"
            % (size, c, s, -s, c, cx, cy, _pdf_string(diagonal))
            + b"q 0.35 g BT /SdaiWmF 7 Tf %.2f %.2f Td %s Tj ET Q\n"
            % (x0 + 12, y0 + 8, _pdf_string(footer))
        )
        overlay = PageObject.create_blank_page(None, w, h)
        overlay[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/SdaiWmF"): font}),
                NameObject("/ExtGState"): DictionaryObject(
                    {NameObject("/SdaiWmGs"): gstate}
                ),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(ops)
        overlay[NameObject("/Contents")] = stream
        page.merge_page(overlay)
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


async def deliver(
    f: DocumentFile, path: Path, filename: str, *, inline: bool, who: str, locale: str
):
    """The response handing out one document file, and whether it was
    watermarked. Confidential/secret PDFs (with the setting on) are stamped
    in memory; a PDF that can't be stamped is refused, never sent bare."""
    from datetime import datetime, timezone

    from fastapi.responses import FileResponse, Response

    headers = {"Content-Disposition": content_disposition(filename, inline)}
    if not f.needs_watermark():
        return FileResponse(path, media_type=f.mime or None, headers=headers), False
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    level = t(f"download.watermark_level_{f.confidentiality}", locale)
    try:
        data = await asyncio.to_thread(
            lambda: watermark_pdf(
                path.read_bytes(),
                f"{level} · {who}",
                t("download.watermark_footer", locale, who=who, when=when),
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=t("download.watermark_failed", locale)
        ) from exc
    return Response(content=data, media_type="application/pdf", headers=headers), True
