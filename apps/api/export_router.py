"""
Selective export with proof of integrity (GED Phase 5) - any signed-in
person, limited to the documents they may DOWNLOAD (unlike the admin
full-tenant export, GET /api/admin/export, which is unchanged).

  POST   /api/db/export                       - start an export job (202)
  GET    /api/db/export/jobs                  - my export jobs, newest first
  GET    /api/db/export/jobs/{id}             - one job
  GET    /api/db/export/jobs/{id}/download    - the ZIP (24 h after it is built)
  DELETE /api/db/export/jobs/{id}             - delete the ZIP now

The selection - explicit documents, a series (optionally with its
sub-series) or a search - is resolved when the job is created, through the
same access clause as the list (at the download level), and frozen in the
job. A background task then builds, under DATA_DIR/generated/exports/
<tenant>/<job id>.zip (opened exclusively, never overwritten):

  files/...          the originals - current version, or every version
  metadata.json      every document: identity, classification, fields, files + sha256
  metadata.csv       the same, one row per document (Excel-friendly, UTF-8 BOM)
  manifest.sha256    sha256 of every other entry ("sha256sum -c manifest.sha256")
  LISEZMOI.txt / README.txt

A file whose content no longer matches its recorded hash, or that is not
on this node (sync still copying it), is listed in metadata.json and the
job's warnings - never silently left out. Jobs are node-local (the ZIP
lives on this node's disk) - sdai_export_jobs is never synced. Audit:
documents_exported (the document ids) when the ZIP is built,
export_downloaded when it is fetched.
"""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import document_files
import ingest_router
from audit import client_ip, log_action
from auth import CurrentUser, require_user
from documents_router import (
    _BASE_COLS,
    _document_visible,
    _get_tables_columns,
    _run_list_query,
    _sanitize,
)
from export_format import serial
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from i18n import t
from ingest_router import _engine
from pydantic import BaseModel, Field, model_validator
from rate_limit import limiter
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db/export", tags=["export"])

EXPORT_MAX_DOCUMENTS = int(os.getenv("EXPORT_MAX_DOCUMENTS", "500"))
EXPORT_MAX_MB = int(os.getenv("EXPORT_MAX_MB", "2048"))
EXPORT_LINK_HOURS = int(os.getenv("EXPORT_LINK_HOURS", "24"))
# Jobs building at the same time on this node (each reads and hashes every file).
_RUNNING = asyncio.Semaphore(int(os.getenv("EXPORT_CONCURRENCY", "2")))
_TASKS: set[asyncio.Task] = set()


def exports_dir() -> Path:
    return ingest_router.DATA_DIR / "generated" / "exports"


# Document-level properties exported for every document besides its
# extracted fields - never server paths, hashes of derived images or sync
# bookkeeping.
_DOC_PROPS = (
    "record_id",
    "document_type",
    "review_status",
    "confidence",
    "confidentiality",
    "lifecycle_status",
    "current_version",
    "ingested_at",
    "reviewed_at",
)


# ── Request ───────────────────────────────────────────────────────────────────


class DocumentRef(BaseModel):
    table_name: str = Field(..., max_length=63)
    id: uuid.UUID


class SearchSelection(BaseModel):
    q: str | None = Field(None, max_length=500)
    document_type: str | None = None
    confidence: str | None = None
    review_status: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    series: uuid.UUID | None = None
    include_subseries: bool = False
    tag: uuid.UUID | None = None
    reviewed_by: uuid.UUID | None = None


class ExportBody(BaseModel):
    documents: list[DocumentRef] | None = Field(None, max_length=1000)
    series_id: uuid.UUID | None = None
    include_sub: bool = True
    search: SearchSelection | None = None
    versions: Literal["current", "all"] = "current"

    @model_validator(mode="after")
    def _one_selection(self):
        given = [x is not None for x in (self.documents, self.series_id, self.search)]
        if sum(given) != 1:
            raise ValueError("give exactly one of documents, series_id or search")
        return self


async def _resolve_selection(
    conn, body: ExportBody, user: CurrentUser
) -> list[tuple[str, str]]:
    """(table, id) of every selected document the user may download, in a
    stable order. Explicit ids the user can't download are dropped (never
    reported - their existence isn't revealed)."""
    tables_cols = await _get_tables_columns(conn, user.tenant_slug)
    if body.documents is not None:
        out: list[tuple[str, str]] = []
        seen = set()
        for ref in body.documents:
            key = (_sanitize(ref.table_name), str(ref.id))
            if key in seen or key[0] not in tables_cols:
                continue
            seen.add(key)
            if await _document_visible(conn, key[0], key[1], user, "download"):
                out.append(key)
        return out
    if body.series_id is not None:
        filters = {"series": str(body.series_id), "include_subseries": body.include_sub}
        only_table = None
        q = None
        other = {}
    else:
        s = body.search
        filters = {
            "series": str(s.series) if s.series else None,
            "include_subseries": s.include_subseries,
            "tag": str(s.tag) if s.tag else None,
        }
        only_table = _sanitize(s.document_type) if s.document_type else None
        if only_table and only_table not in tables_cols:
            return []
        q = s.q or None
        other = {
            "confidence": s.confidence,
            "review_status": s.review_status,
            "date_from": s.date_from,
            "date_to": s.date_to,
            "reviewed_by": str(s.reviewed_by) if s.reviewed_by else None,
        }
    rows, _total = await _run_list_query(
        conn,
        tables_cols,
        only_table=only_table,
        q=q,
        confidence=other.get("confidence"),
        review_status=other.get("review_status"),
        date_from=other.get("date_from"),
        date_to=other.get("date_to"),
        reviewed_by=other.get("reviewed_by"),
        page=1,
        page_size=EXPORT_MAX_DOCUMENTS + 1,
        current_user=user,
        permission="download",
        with_details=False,
        **filters,
    )
    return [(r["table_name"], r["id"]) for r in rows]


async def _versions_of(
    conn, tenant_id: str, docs: list[tuple[str, str]], mode: str
) -> list[dict]:
    """The version rows to export, per document (current only or all)."""
    out = []
    for table, doc_id in docs:
        rows = (
            (
                await conn.execute(
                    text(f"""
                        SELECT v.version_no, v.file_path, v.file_hash, v.size, v.mime,
                               v.original_filename, v.created_at,
                               (v.version_no = COALESCE(d.current_version, 1)) AS is_current
                        FROM sdai_document_versions v
                        JOIN "{table}" d ON d.id = v.document_id
                        WHERE v.tenant_id = CAST(:tid AS uuid) AND v.table_name = :t
                          AND v.document_id = CAST(:id AS uuid)
                          {"" if mode == "all" else "AND v.version_no = COALESCE(d.current_version, 1)"}
                        ORDER BY v.version_no
                    """),
                    {"tid": tenant_id, "t": table, "id": doc_id},
                )
            )
            .mappings()
            .all()
        )
        out.extend(
            {"table_name": table, "document_id": doc_id, **dict(r)} for r in rows
        )
    return out


def _file_size(v: dict) -> int:
    if v.get("size"):
        return int(v["size"])
    try:
        return os.path.getsize(v["file_path"])
    except OSError:
        return 0


def _job_json(row) -> dict:
    data = {
        k: serial(v)
        for k, v in dict(row).items()
        if k not in ("file_path", "documents")
    }
    data["selection"] = row["selection"]
    return data


_JOB_COLS = """
    id, status, selection, versions, document_count, file_count, total_bytes, zip_sha256,
    warnings, error, created_at, started_at, finished_at, expires_at, file_path
"""


async def _purge_expired() -> None:
    """Delete ZIPs past their window (called on every job list/create and
    at startup - no separate worker)."""
    async with _engine().begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "UPDATE sdai_export_jobs SET status = 'expired'"
                    " WHERE status = 'done' AND expires_at <= now() RETURNING file_path"
                )
            )
        ).all()
    for (path,) in rows:
        if path:
            Path(path).unlink(missing_ok=True)


async def recover_jobs() -> None:
    """At startup: jobs a restart interrupted are failed (their partial ZIP
    removed), expired ZIPs deleted."""
    async with _engine().begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "UPDATE sdai_export_jobs SET status = 'failed', error = 'interrupted',"
                    " finished_at = now() WHERE status IN ('queued', 'running') RETURNING file_path"
                )
            )
        ).all()
    for (path,) in rows:
        if path:
            Path(path).unlink(missing_ok=True)
    await _purge_expired()


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "", status_code=202, summary="Export a selection of documents with their files"
)
@limiter.limit("10/minute")
async def create_export(
    request: Request,
    body: ExportBody,
    current_user: CurrentUser = Depends(require_user),
):
    locale = current_user.locale
    await _purge_expired()
    async with _engine().begin() as conn:
        docs = await _resolve_selection(conn, body, current_user)
        if not docs:
            raise HTTPException(status_code=422, detail=t("exports.empty", locale))
        if len(docs) > EXPORT_MAX_DOCUMENTS:
            raise HTTPException(
                status_code=413,
                detail=t("exports.too_many", locale, max=EXPORT_MAX_DOCUMENTS),
            )
        versions = await _versions_of(conn, current_user.tenant_id, docs, body.versions)
        total = sum(_file_size(v) for v in versions)
        if total > EXPORT_MAX_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=t(
                    "exports.too_large",
                    locale,
                    size=total // (1024 * 1024),
                    max=EXPORT_MAX_MB,
                ),
            )
        selection = body.model_dump(
            mode="json", exclude={"documents"}, exclude_none=True
        )
        selection["kind"] = (
            "documents"
            if body.documents is not None
            else "series"
            if body.series_id
            else "search"
        )
        row = (
            (
                await conn.execute(
                    text(f"""
                        INSERT INTO sdai_export_jobs
                            (tenant_id, created_by, selection, versions, documents,
                             document_count, file_count, total_bytes)
                        VALUES (CAST(:tid AS uuid), CAST(:by AS uuid), CAST(:sel AS jsonb), :v,
                                CAST(:docs AS jsonb), :n, :files, :bytes)
                        RETURNING {_JOB_COLS}
                    """),
                    {
                        "tid": current_user.tenant_id,
                        "by": current_user.id,
                        "sel": json.dumps(selection),
                        "v": body.versions,
                        "docs": json.dumps(
                            [{"table_name": tb, "id": i} for tb, i in docs]
                        ),
                        "n": len(docs),
                        "files": len(versions),
                        "bytes": total,
                    },
                )
            )
            .mappings()
            .one()
        )
    job_id = str(row["id"])
    task = asyncio.create_task(
        _run_job(job_id, current_user, docs, body.versions, client_ip(request))
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return _job_json(row)


async def _own_job(conn, job_id: str, user: CurrentUser, lock: bool = False):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=t("exports.not_found", user.locale)
        ) from exc
    row = (
        (
            await conn.execute(
                text(f"""
                    SELECT {_JOB_COLS} FROM sdai_export_jobs
                    WHERE id = CAST(:id AS uuid) AND created_by = CAST(:me AS uuid)
                      AND tenant_id = CAST(:tid AS uuid)
                    {"FOR UPDATE" if lock else ""}
                """),
                {"id": job_id, "me": user.id, "tid": user.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=t("exports.not_found", user.locale))
    return row


@router.get("/jobs", summary="My export jobs")
async def list_jobs(current_user: CurrentUser = Depends(require_user)):
    await _purge_expired()
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                        SELECT {_JOB_COLS} FROM sdai_export_jobs
                        WHERE created_by = CAST(:me AS uuid) AND tenant_id = CAST(:tid AS uuid)
                          AND status <> 'deleted'
                        ORDER BY created_at DESC LIMIT 50
                    """),
                    {"me": current_user.id, "tid": current_user.tenant_id},
                )
            )
            .mappings()
            .all()
        )
    return {"jobs": [_job_json(r) for r in rows]}


@router.get("/jobs/{job_id}", summary="One export job")
async def get_job(job_id: str, current_user: CurrentUser = Depends(require_user)):
    async with _engine().connect() as conn:
        return _job_json(await _own_job(conn, job_id, current_user))


@router.get("/jobs/{job_id}/download", summary="Download an export ZIP")
@limiter.limit("30/minute")
async def download_job(
    request: Request, job_id: str, current_user: CurrentUser = Depends(require_user)
):
    locale = current_user.locale
    async with _engine().connect() as conn:
        row = await _own_job(conn, job_id, current_user)
    if row["status"] != "done" or row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail=t("exports.not_available", locale))
    path = Path(row["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail=t("exports.not_available", locale))
    await log_action(
        action="export_downloaded",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"job_id": job_id, "zip_sha256": row["zip_sha256"]},
        ip_address=client_ip(request),
    )
    name = f"export_{row['created_at'].strftime('%Y%m%d_%H%M')}_{job_id[:8]}.zip"
    return FileResponse(
        path,
        media_type="application/zip",
        headers={"Content-Disposition": document_files.content_disposition(name)},
    )


@router.delete("/jobs/{job_id}", summary="Delete an export ZIP now")
async def delete_job(job_id: str, current_user: CurrentUser = Depends(require_user)):
    async with _engine().begin() as conn:
        row = await _own_job(conn, job_id, current_user, lock=True)
        if row["status"] in ("queued", "running"):
            raise HTTPException(
                status_code=409, detail=t("exports.still_running", current_user.locale)
            )
        await conn.execute(
            text(
                "UPDATE sdai_export_jobs SET status = 'deleted' WHERE id = CAST(:id AS uuid)"
            ),
            {"id": job_id},
        )
    if row["file_path"]:
        Path(row["file_path"]).unlink(missing_ok=True)
    return {"deleted": True}


# ── Building the ZIP ──────────────────────────────────────────────────────────


async def _gather(conn, user: CurrentUser, docs: list[tuple[str, str]], mode: str):
    """Everything the ZIP needs from the database, read in one go."""
    tables_cols = await _get_tables_columns(conn, user.tenant_slug)
    series = {
        str(r[0]): {"name": r[1], "code": r[2], "path": r[3]}
        for r in await conn.execute(
            text(
                "SELECT id, name, code, path FROM sdai_series WHERE tenant_id = CAST(:t AS uuid)"
            ),
            {"t": user.tenant_id},
        )
    }
    documents = []
    for table, doc_id in docs:
        cols = tables_cols.get(table)
        if not cols:
            continue
        row = (
            (
                await conn.execute(
                    text(f'SELECT * FROM "{table}" WHERE id = CAST(:id AS uuid)'),
                    {"id": doc_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            continue
        row = dict(row)
        tags = [
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT tg.name FROM sdai_document_tags dt JOIN sdai_tags tg ON tg.id = dt.tag_id"
                    " WHERE dt.tenant_id = CAST(:t AS uuid) AND dt.table_name = :tb"
                    " AND dt.document_id = CAST(:id AS uuid) ORDER BY tg.name"
                ),
                {"t": user.tenant_id, "tb": table, "id": doc_id},
            )
        ]
        series_path = None
        if row.get("series_id"):
            s = series.get(str(row["series_id"]))
            if s:
                ids = [p for p in (s["path"] or "").split("/") if p]
                series_path = " / ".join(series[i]["name"] for i in ids if i in series)
        documents.append(
            {
                "table_name": table,
                "id": doc_id,
                **{p: serial(row.get(p)) for p in _DOC_PROPS if p in row},
                "series": series_path,
                "tags": tags,
                "fields": {
                    c: serial(v)
                    for c, v in row.items()
                    if c not in _BASE_COLS and c not in _DOC_PROPS
                },
            }
        )
    versions = await _versions_of(conn, user.tenant_id, docs, mode)
    return documents, versions


def _readme(lang: str, ctx: dict) -> str:
    if lang == "fr":
        return f"""EXPORT DE DOCUMENTS - SDAI
==========================

Exporté le      : {ctx["exported_at"]} (UTC)
Exporté par     : {ctx["user"]}
Serveur         : {ctx["node"]}
Documents       : {ctx["documents"]}
Fichiers        : {ctx["files"]} ({"toutes les versions" if ctx["mode"] == "all" else "version courante"})

Contenu
-------
files/            les fichiers originaux, tels qu'archivés
metadata.json     chaque document : identifiant, classement, champs extraits,
                  fichiers et leur empreinte SHA-256
metadata.csv      les mêmes informations, une ligne par document (Excel)
manifest.sha256   l'empreinte SHA-256 de chacun des autres fichiers de l'archive

Vérifier l'intégrité
--------------------
Linux / macOS :   sha256sum -c manifest.sha256     (macOS : shasum -a 256 -c manifest.sha256)
Windows       :   CertUtil -hashfile "files\\<fichier>" SHA256
                  puis comparer avec la ligne correspondante de manifest.sha256.

Toute différence signifie que le fichier a été modifié après l'export.
Les empreintes des originaux sont aussi celles enregistrées dans la GED au
moment de leur archivage (champ "sha256" de metadata.json).
{ctx["warnings_fr"]}"""
    return f"""SDAI DOCUMENT EXPORT
====================

Exported at     : {ctx["exported_at"]} (UTC)
Exported by     : {ctx["user"]}
Server          : {ctx["node"]}
Documents       : {ctx["documents"]}
Files           : {ctx["files"]} ({"every version" if ctx["mode"] == "all" else "current version"})

Contents
--------
files/            the original files, as archived
metadata.json     every document: identifier, classification, extracted fields,
                  files and their SHA-256 hash
metadata.csv      the same, one row per document (Excel)
manifest.sha256   the SHA-256 hash of every other file in this archive

Checking integrity
------------------
Linux / macOS :   sha256sum -c manifest.sha256     (macOS: shasum -a 256 -c manifest.sha256)
Windows       :   CertUtil -hashfile "files\\<file>" SHA256
                  and compare with the matching line of manifest.sha256.

Any difference means the file was changed after the export. The originals'
hashes are also the ones the archive recorded when they were filed
("sha256" in metadata.json).
{ctx["warnings_en"]}"""


def _csv(documents: list[dict]) -> str:
    field_names: list[str] = []
    for d in documents:
        for k in d["fields"]:
            if k not in field_names:
                field_names.append(k)
    head = ["table_name", "id", *_DOC_PROPS, "series"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([*head, "tags", "files", *field_names])

    def cell(v):
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return v

    for d in documents:
        w.writerow(
            [cell(d.get(h)) for h in head]
            + [
                "; ".join(d["tags"]),
                "; ".join(f["path"] for f in d["files"] if f.get("path")),
            ]
            + [cell(d["fields"].get(k)) for k in field_names]
        )
    return "﻿" + out.getvalue()


def _build_zip(
    dest: Path, documents: list[dict], versions: list[dict], ctx: dict
) -> dict:
    """Write the ZIP (exclusively - never over an existing file). Returns
    {zip_sha256, warnings}. Runs in a thread."""
    by_doc: dict[tuple[str, str], list[dict]] = {}
    for v in versions:
        by_doc.setdefault((v["table_name"], v["document_id"]), []).append(v)
    used: set[str] = set()
    manifest: list[tuple[str, str]] = []
    warnings: list[dict] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "xb") as raw, zipfile.ZipFile(raw, "w", allowZip64=True) as zf:
        for d in documents:
            d["files"] = []
            label = d.get("record_id") or f"{d['table_name']}_{d['id'][:8]}"
            for v in by_doc.get((d["table_name"], d["id"]), []):
                name = document_files.download_name(
                    d.get("record_id"),
                    v["original_filename"],
                    Path(v["file_path"]).name,
                )
                if ctx["mode"] == "all":
                    name = f"{document_files.download_name(None, label, 'document')}/v{v['version_no']}_{Path(name).name}"
                arc = f"files/{name}"
                n = 2
                while arc in used:
                    stem, ext = os.path.splitext(f"files/{name}")
                    arc = f"{stem}_{n}{ext}"
                    n += 1
                entry = {
                    "version_no": v["version_no"],
                    "is_current": bool(v["is_current"]),
                    "original_filename": v["original_filename"],
                    "mime": v["mime"],
                    "added_at": serial(v["created_at"]),
                    "recorded_sha256": v["file_hash"],
                }
                path = document_files.safe_path(v["file_path"])
                if path is None or not path.is_file():
                    entry["status"] = "missing"
                    warnings.append(
                        {
                            "document": d["id"],
                            "version_no": v["version_no"],
                            "problem": "missing",
                        }
                    )
                    d["files"].append(entry)
                    continue
                h = hashlib.sha256()
                size = 0
                compress = (
                    zipfile.ZIP_DEFLATED
                    if path.suffix.lower() in (".txt", ".csv", ".json")
                    else zipfile.ZIP_STORED
                )
                info = zipfile.ZipInfo.from_file(path, arc)
                info.compress_type = compress
                with (
                    open(path, "rb") as src,
                    zf.open(info, "w", force_zip64=True) as out,
                ):
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        h.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
                digest = h.hexdigest()
                used.add(arc)
                manifest.append((digest, arc))
                entry.update({"path": arc, "sha256": digest, "size": size})
                if v["file_hash"] and v["file_hash"] != digest:
                    entry["status"] = "hash_mismatch"
                    warnings.append(
                        {
                            "document": d["id"],
                            "version_no": v["version_no"],
                            "problem": "hash_mismatch",
                        }
                    )
                else:
                    entry["status"] = "ok" if v["file_hash"] else "unverified"
                d["files"].append(entry)

        if warnings:
            ctx["warnings_fr"] = (
                f"\nATTENTION : {len(warnings)} fichier(s) manquant(s) ou dont l'empreinte ne"
                ' correspond plus - voir "status" dans metadata.json.\n'
            )
            ctx["warnings_en"] = (
                f"\nWARNING: {len(warnings)} file(s) missing or no longer matching their recorded"
                ' hash - see "status" in metadata.json.\n'
            )
        else:
            ctx["warnings_fr"] = ctx["warnings_en"] = ""
        meta = {
            "exported_at": ctx["exported_at"],
            "exported_by": ctx["user"],
            "tenant": ctx["tenant"],
            "node": ctx["node"],
            "job_id": ctx["job_id"],
            "versions": ctx["mode"],
            "documents": documents,
            "warnings": warnings,
        }
        extras = {
            "metadata.json": json.dumps(meta, ensure_ascii=False, indent=2),
            "metadata.csv": _csv(documents),
            "LISEZMOI.txt": _readme("fr", ctx),
            "README.txt": _readme("en", ctx),
        }
        for arc, content in extras.items():
            data = content.encode("utf-8")
            zf.writestr(arc, data, compress_type=zipfile.ZIP_DEFLATED)
            manifest.append((hashlib.sha256(data).hexdigest(), arc))
        zf.writestr(
            "manifest.sha256",
            "".join(f"{digest}  {arc}\n" for digest, arc in manifest),
            compress_type=zipfile.ZIP_DEFLATED,
        )
    h = hashlib.sha256()
    with open(dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return {"zip_sha256": h.hexdigest(), "warnings": warnings}


async def _run_job(
    job_id: str, user: CurrentUser, docs, mode: str, ip: str | None
) -> None:
    dest = exports_dir() / user.tenant_slug / f"{job_id}.zip"
    try:
        async with _RUNNING:
            async with _engine().begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE sdai_export_jobs SET status = 'running', started_at = now(),"
                        " file_path = :p WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": job_id, "p": str(dest)},
                )
            async with _engine().connect() as conn:
                documents, versions = await _gather(conn, user, docs, mode)
            ctx = {
                "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "user": user.email,
                "tenant": user.tenant_slug,
                "node": ingest_router.NODE_ID,
                "job_id": job_id,
                "mode": mode,
                "documents": len(documents),
                "files": len(versions),
            }
            result = await asyncio.to_thread(_build_zip, dest, documents, versions, ctx)
            size = dest.stat().st_size
            async with _engine().begin() as conn:
                await conn.execute(
                    text("""
                        UPDATE sdai_export_jobs
                        SET status = 'done', finished_at = now(), zip_sha256 = :h,
                            warnings = CAST(:w AS jsonb), total_bytes = :size,
                            expires_at = now() + make_interval(hours => :hours)
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {
                        "id": job_id,
                        "h": result["zip_sha256"],
                        "w": json.dumps(result["warnings"]),
                        "size": size,
                        "hours": EXPORT_LINK_HOURS,
                    },
                )
        await log_action(
            action="documents_exported",
            user_id=user.id,
            user_email=user.email,
            details={
                "job_id": job_id,
                "versions": mode,
                "count": len(docs),
                "documents": [{"table_name": tb, "id": i} for tb, i in docs],
                "zip_sha256": result["zip_sha256"],
                "warnings": len(result["warnings"]),
            },
            ip_address=ip,
        )
    except Exception as exc:  # noqa: BLE001 - the job records any failure
        logger.exception("export job %s failed", job_id)
        dest.unlink(missing_ok=True)
        async with _engine().begin() as conn:
            await conn.execute(
                text(
                    "UPDATE sdai_export_jobs SET status = 'failed', finished_at = now(), error = :e"
                    " WHERE id = CAST(:id AS uuid)"
                ),
                {"id": job_id, "e": type(exc).__name__},
            )


async def wait_idle(timeout: float = 30) -> None:
    """Wait for the export jobs started on this process (tests, shutdown)."""
    if _TASKS:
        await asyncio.wait(list(_TASKS), timeout=timeout)
