"""Destruction (GED Phase 6.4) - the only way a document's content and
files leave the system, always with a record and a report.

  propose   a lifecycle manager lists destruction_eligible documents
            (sdai_destruction_records, status 'proposed'). The list is frozen
            with everything that stays known afterwards: record_id, type,
            series, dates and the hash of every file (versions, original).
  approve   an admin who is NOT the proposer (also enforced by a CHECK in
            the database) approves - in the same transaction every document
            becomes a tombstone (lifecycle.tombstone), its versions, field
            history, pages, grants and tags are deleted, share links revoked
            and every file it used is listed in sdai_destroyed_files. If one
            document can't be destroyed (legal hold set meanwhile, status
            changed) nothing is: 409. After the commit the files are deleted
            on this node, and the report (PV) is generated.
  reject / cancel   an admin rejects, the proposer cancels - nothing changes.
  purge     the daily job destroys documents in the trash for longer than
            TRASH_RETENTION_DAYS through the same path, with an automatic
            approval record ("purge corbeille") - kind 'trash_purge'.

Both nodes: sdai_destroyed_files is synced (insert-only); every node deletes
the listed files it still has (purge_destroyed_files - right after applying
a peer's changes and every few minutes) and never transfers them again (the
sync file queue skips them, the sync file API refuses them). Links to a
destroyed document stay and show its tombstone.

Backups (restic) keep the files until their retention expires - the PV and
RESTORE.md say so.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lifecycle
from i18n import format_date, format_datetime
from sqlalchemy import text

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
PURGE_BATCH = int(os.getenv("TRASH_PURGE_BATCH", "500"))
DESTROYED_FILES_SWEEP_SECONDS = int(os.getenv("DESTROYED_FILES_SWEEP_SECONDS", "600"))


class DestructionError(Exception):
    def __init__(self, status: int, key: str, **params):
        super().__init__(key)
        self.status = status
        self.key = key
        self.params = params


def pv_dir(tenant_slug: str) -> Path:
    return DATA_DIR / "generated" / "destruction" / tenant_slug


def record_number(record_id: str, proposed_at: datetime | None) -> str:
    year = (proposed_at or datetime.now(timezone.utc)).year
    return f"ELIM-{year}-{record_id[:8].upper()}"


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ── Snapshot ──────────────────────────────────────────────────────────────────


async def snapshot(conn, tenant_id: str, table: str, doc_id: str) -> dict:
    """What stays known about a document after its destruction - frozen in
    the destruction record when it is proposed (and in the PV)."""
    row = (
        (
            await conn.execute(
                text(f"""
                SELECT d.id::text AS id, d.record_id, d.ingested_at, d.archived_at,
                       d.retention_until, d.deleted_at, d.content_hash, d.original_hash,
                       COALESCE(d.lifecycle_status, 'active') AS lifecycle_status,
                       d.confidentiality, d.retention_rule_id::text AS rule_id,
                       r.name AS rule_name, s.name AS series_name, s.code AS series_code
                FROM "{table}" d
                LEFT JOIN sdai_series s ON s.id = d.series_id
                LEFT JOIN sdai_retention_rules r ON r.id = d.retention_rule_id
                WHERE d.id = CAST(:id AS uuid)
            """),
                {"id": doc_id},
            )
        )
        .mappings()
        .one()
    )
    doc_type = None
    has_type = (
        await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                " AND table_name = :t AND column_name = 'document_type'"
            ),
            {"t": table},
        )
    ).one_or_none()
    if has_type:
        doc_type = (
            await conn.execute(
                text(
                    f'SELECT document_type::text FROM "{table}" WHERE id = CAST(:id AS uuid)'
                ),
                {"id": doc_id},
            )
        ).scalar()
    versions = (
        (
            await conn.execute(
                text("""
                SELECT version_no, file_hash, original_filename, size, created_at
                FROM sdai_document_versions
                WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
                  AND document_id = CAST(:id AS uuid)
                ORDER BY version_no
            """),
                {"tid": tenant_id, "t": table, "id": doc_id},
            )
        )
        .mappings()
        .all()
    )
    return {
        "table_name": table,
        "id": row["id"],
        "record_id": row["record_id"],
        "document_type": doc_type or table,
        "series": " ".join(x for x in (row["series_code"], row["series_name"]) if x)
        or None,
        "confidentiality": row["confidentiality"],
        "ingested_at": _iso(row["ingested_at"]),
        "archived_at": _iso(row["archived_at"]),
        "retention_until": _iso(row["retention_until"]),
        "trashed_at": _iso(row["deleted_at"]),
        "rule": row["rule_name"],
        "content_hash": row["content_hash"],
        "original_hash": row["original_hash"],
        "versions": [
            {
                "version_no": v["version_no"],
                "file_hash": v["file_hash"],
                "filename": v["original_filename"],
                "size": v["size"],
                "created_at": _iso(v["created_at"]),
            }
            for v in versions
        ],
    }


# ── Files of a document ───────────────────────────────────────────────────────


async def _uploads_of(conn, table: str, doc_id: str) -> list[str]:
    """batch_documents rows that produced or re-extracted into the document."""
    bd = (
        await conn.execute(
            text(
                f'SELECT batch_document_id::text FROM "{table}" WHERE id = CAST(:id AS uuid)'
            ),
            {"id": doc_id},
        )
    ).scalar()
    rows = (
        (
            await conn.execute(
                text("""
                SELECT id::text FROM batch_documents
                WHERE id = CAST(:bd AS uuid)
                   OR (target_table = :t AND target_document_id = CAST(:id AS uuid))
            """),
                {"bd": bd, "t": table, "id": doc_id},
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def files_of(
    conn, tenant_id: str, table: str, doc_id: str
) -> dict[str, str | None]:
    """{path: sha256 or None} - every file the document uses: its own file
    columns, every version's file, its uploads' source files and page
    images."""
    files: dict[str, str | None] = {}

    def add(path, sha=None):
        if isinstance(path, str) and path and os.path.isabs(path):
            files[path] = files.get(path) or sha

    row = (
        (
            await conn.execute(
                text(f"""
                SELECT source_image_path, source_pdf_path, page_image_paths, original_path,
                       original_hash
                FROM "{table}" WHERE id = CAST(:id AS uuid)
            """),
                {"id": doc_id},
            )
        )
        .mappings()
        .one()
    )
    add(row["source_image_path"])
    add(row["source_pdf_path"])
    add(row["original_path"], row["original_hash"])
    pages = row["page_image_paths"]
    if isinstance(pages, str):
        try:
            pages = json.loads(pages)
        except ValueError:
            pages = []
    for p in pages or []:
        add(p)
    for path, sha in await conn.execute(
        text("""
            SELECT file_path, file_hash FROM sdai_document_versions
            WHERE tenant_id = CAST(:tid AS uuid) AND table_name = :t
              AND document_id = CAST(:id AS uuid)
        """),
        {"tid": tenant_id, "t": table, "id": doc_id},
    ):
        add(path, sha)
    uploads = await _uploads_of(conn, table, doc_id)
    if uploads:
        for path, image in await conn.execute(
            text(
                "SELECT source_path, image_path FROM batch_documents"
                " WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": uploads},
        ):
            add(path)
            add(image)
        for (image,) in await conn.execute(
            text(
                "SELECT image_path FROM batch_document_pages"
                " WHERE batch_document_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": uploads},
        ):
            add(image)
    return files


async def _still_used(
    conn, tenant_slug: str, paths: list[str], destroyed: set
) -> set[str]:
    """Paths among `paths` some other non-destroyed document still uses -
    never deleted (a file can be shared, e.g. a version restored from
    another document's file)."""
    if not paths:
        return set()
    used: set[str] = set()
    tables = (
        (
            await conn.execute(
                text("""
                SELECT DISTINCT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'source_image_path'
                  AND starts_with(table_name, :prefix)
            """),
                {"prefix": f"t_{tenant_slug}_"},
            )
        )
        .scalars()
        .all()
    )
    for table in tables:
        ids = [i for t, i in destroyed if t == table] or [
            "00000000-0000-0000-0000-000000000000"
        ]
        rows = await conn.execute(
            text(f"""
                SELECT p FROM unnest(CAST(:paths AS text[])) p
                WHERE EXISTS (
                    SELECT 1 FROM "{table}" d
                    WHERE d.id <> ALL(CAST(:ids AS uuid[]))
                      AND d.lifecycle_status IS DISTINCT FROM 'destroyed'
                      AND (d.source_image_path = p OR d.source_pdf_path = p
                           OR d.original_path = p OR d.page_image_paths ? p))
            """),
            {"paths": paths, "ids": ids},
        )
        used.update(r[0] for r in rows)
    rows = await conn.execute(
        text("""
            SELECT DISTINCT file_path FROM sdai_document_versions
            WHERE file_path = ANY(CAST(:paths AS text[]))
        """),
        {"paths": paths},
    )
    used.update(
        r[0] for r in rows
    )  # the destroyed documents' versions are deleted first
    return used


# ── Proposal / decision ───────────────────────────────────────────────────────


async def _pending_with(conn, tenant_id: str, table: str, doc_id: str) -> str | None:
    return (
        await conn.execute(
            text("""
                SELECT id::text FROM sdai_destruction_records
                WHERE tenant_id = CAST(:tid AS uuid) AND status = 'proposed'
                  AND documents @> CAST(:probe AS jsonb)
                LIMIT 1
            """),
            {
                "tid": tenant_id,
                "probe": json.dumps([{"table_name": table, "id": doc_id}]),
            },
        )
    ).scalar()


async def propose(
    conn, user, documents: list[tuple[str, str]], comment: str | None
) -> dict:
    """A destruction proposal for destruction_eligible, unheld documents not
    already in another pending proposal. Returns the record row."""
    if not documents:
        raise DestructionError(422, "destruction.empty")
    snapshots = []
    seen = set()
    for table, doc_id in documents:
        if (table, doc_id) in seen:
            continue
        seen.add((table, doc_id))
        row = await lifecycle.load(conn, table, doc_id, lock=True)
        if row is None:
            raise DestructionError(404, "common.document_not_found")
        if (
            row["lifecycle_status"] != "destruction_eligible"
            or row["deleted_at"] is not None
        ):
            raise DestructionError(
                409, "destruction.not_eligible", record=row["record_id"] or doc_id
            )
        if row["held"]:
            raise DestructionError(
                409, "destruction.held", record=row["record_id"] or doc_id
            )
        if await _pending_with(conn, user.tenant_id, table, doc_id):
            raise DestructionError(
                409, "destruction.already_proposed", record=row["record_id"] or doc_id
            )
        snapshots.append(await snapshot(conn, user.tenant_id, table, doc_id))
    record = (
        (
            await conn.execute(
                text("""
                INSERT INTO sdai_destruction_records
                    (tenant_id, kind, status, comment, documents, document_count, proposed_by)
                VALUES (CAST(:tid AS uuid), 'retention', 'proposed', :c, CAST(:docs AS jsonb),
                        :n, CAST(:u AS uuid))
                RETURNING id::text AS id, proposed_at
            """),
                {
                    "tid": user.tenant_id,
                    "c": comment,
                    "docs": json.dumps(snapshots),
                    "n": len(snapshots),
                    "u": user.id,
                },
            )
        )
        .mappings()
        .one()
    )
    return {
        "id": record["id"],
        "proposed_at": record["proposed_at"],
        "documents": snapshots,
    }


async def load_record(
    conn, tenant_id: str, record_id: str, *, lock: bool = False
) -> dict:
    row = (
        (
            await conn.execute(
                text(f"""
                SELECT r.id::text AS id, r.kind, r.status, r.comment, r.documents,
                       r.document_count, r.proposed_by::text AS proposed_by, r.proposed_at,
                       r.decided_by::text AS decided_by, r.decided_at, r.decision_note,
                       r.executed_at, r.file_path, r.file_hash,
                       pu.email AS proposed_by_email, du.email AS decided_by_email,
                       -- The PV is a sealed record: it keeps the real address of
                       -- someone deleted since (screens show the tombstone).
                       COALESCE(pu.deleted_email, pu.email) AS pv_proposed_by_email,
                       COALESCE(du.deleted_email, du.email) AS pv_decided_by_email
                FROM sdai_destruction_records r
                LEFT JOIN sdai_users pu ON pu.id = r.proposed_by
                LEFT JOIN sdai_users du ON du.id = r.decided_by
                WHERE r.id = CAST(:id AS uuid) AND r.tenant_id = CAST(:tid AS uuid)
                {"FOR UPDATE OF r" if lock else ""}
            """),
                {"id": record_id, "tid": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DestructionError(404, "destruction.not_found")
    return dict(row)


async def decide(conn, user, record_id: str, approve: bool, note: str | None) -> dict:
    """Approve (and execute, in this transaction) or reject a proposal. The
    approver must be an admin other than the proposer. Returns the record
    plus the files to delete after commit ("files")."""
    record = await load_record(conn, user.tenant_id, record_id, lock=True)
    if record["status"] != "proposed":
        raise DestructionError(409, "destruction.not_pending")
    if record["proposed_by"] == user.id:
        raise DestructionError(403, "destruction.same_person")
    await conn.execute(
        text("""
            UPDATE sdai_destruction_records
            SET status = :s, decided_by = CAST(:u AS uuid), decided_at = now(),
                decision_note = :n
            WHERE id = CAST(:id AS uuid)
        """),
        {
            "s": "approved" if approve else "rejected",
            "u": user.id,
            "n": note,
            "id": record_id,
        },
    )
    record["files"] = []
    if approve:
        record["files"] = await execute(
            conn,
            user.tenant_id,
            user.tenant_slug,
            record_id,
            record["documents"],
            "destruction",
        )
    return record


async def cancel(conn, user, record_id: str) -> None:
    record = await load_record(conn, user.tenant_id, record_id, lock=True)
    if record["status"] != "proposed":
        raise DestructionError(409, "destruction.not_pending")
    if record["proposed_by"] != user.id and user.role != "admin":
        raise DestructionError(403, "destruction.not_proposer")
    await conn.execute(
        text(
            "UPDATE sdai_destruction_records SET status = 'cancelled', decided_at = now(),"
            " decision_note = 'cancelled' WHERE id = CAST(:id AS uuid)"
        ),
        {"id": record_id},
    )


# ── Execution ─────────────────────────────────────────────────────────────────


async def execute(
    conn,
    tenant_id: str,
    tenant_slug: str,
    record_id: str,
    documents: list[dict],
    op: str,
) -> list[dict]:
    """Destroy every document of the record, in the caller's transaction
    (all or nothing). Returns [{path, sha256}] of the files to delete -
    already listed in sdai_destroyed_files (so the peer deletes them too);
    the caller deletes them here after commit (purge_destroyed_files)."""
    if isinstance(documents, str):
        documents = json.loads(documents)
    targets = [(d["table_name"], d["id"]) for d in documents]
    destroyed = set(targets)
    all_files: dict[str, str | None] = {}
    for table, doc_id in targets:
        files = await files_of(conn, tenant_id, table, doc_id)
        for path, sha in files.items():
            all_files[path] = all_files.get(path) or sha
        await lifecycle.tombstone(conn, table, doc_id, op, record_id)
        uploads = await _uploads_of(conn, table, doc_id)
        params = {"tid": tenant_id, "t": table, "id": doc_id}
        for statement in (
            "DELETE FROM sdai_document_versions WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_field_history WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_document_tags WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_document_access WHERE tenant_id = CAST(:tid AS uuid)"
            " AND scope = 'document' AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_comments WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_notifications WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_signature_requests WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "DELETE FROM sdai_signatures WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid)",
            "UPDATE sdai_share_links SET revoked_at = now() WHERE tenant_id = CAST(:tid AS uuid)"
            " AND table_name = :t AND document_id = CAST(:id AS uuid) AND revoked_at IS NULL",
        ):
            await conn.execute(text(statement), params)
        if uploads:
            await conn.execute(
                text(
                    "DELETE FROM batch_document_pages"
                    " WHERE batch_document_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": uploads},
            )
            await conn.execute(
                text(
                    "UPDATE batch_documents SET source_path = NULL, image_path = NULL"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": uploads},
            )
    keep = await _still_used(conn, tenant_slug, list(all_files), destroyed)
    to_delete = [
        {"path": p, "sha256": s} for p, s in sorted(all_files.items()) if p not in keep
    ]
    for f in to_delete:
        await conn.execute(
            text("""
                INSERT INTO sdai_destroyed_files (tenant_id, destruction_id, path, sha256)
                VALUES (CAST(:tid AS uuid), CAST(:d AS uuid), :p, :s)
            """),
            {"tid": tenant_id, "d": record_id, "p": f["path"], "s": f["sha256"]},
        )
    await conn.execute(
        text(
            "UPDATE sdai_destruction_records SET executed_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"id": record_id},
    )
    return to_delete


async def purge_trash(engine, tenant_id: str, now: datetime | None = None) -> int:
    """Destroy the tenant's documents that have been in the trash for
    TRASH_RETENTION_DAYS (held ones stay), one automatically approved
    record ("purge corbeille") per run. Returns the number destroyed."""
    from audit import log_action

    now = now or datetime.now(timezone.utc)
    async with engine.begin() as conn:
        tenant = (
            await conn.execute(
                text("SELECT slug FROM sdai_tenants WHERE id = CAST(:tid AS uuid)"),
                {"tid": tenant_id},
            )
        ).scalar()
        due: list[tuple[str, str]] = []
        tables = (
            (
                await conn.execute(
                    text("""
                    SELECT DISTINCT table_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND column_name = 'source_image_path'
                      AND starts_with(table_name, :prefix)
                """),
                    {"prefix": f"t_{tenant}_"},
                )
            )
            .scalars()
            .all()
        )
        cutoff = now - timedelta(days=lifecycle.TRASH_RETENTION_DAYS)
        for table in sorted(tables):
            rows = await conn.execute(
                text(f"""
                    SELECT id::text FROM "{table}"
                    WHERE deleted_at IS NOT NULL AND deleted_at <= :cutoff
                      AND lifecycle_status IS DISTINCT FROM 'destroyed'
                      AND NOT {lifecycle.held_sql(table)}
                    ORDER BY deleted_at LIMIT :n
                """),
                {"cutoff": cutoff, "n": PURGE_BATCH - len(due)},
            )
            due += [(table, r[0]) for r in rows]
            if len(due) >= PURGE_BATCH:
                break
        if not due:
            return 0
        snapshots = [await snapshot(conn, tenant_id, t, i) for t, i in due]
        record_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_destruction_records
                        (tenant_id, kind, status, comment, documents, document_count,
                         decided_at, decision_note)
                    VALUES (CAST(:tid AS uuid), 'trash_purge', 'approved', 'purge corbeille',
                            CAST(:docs AS jsonb), :n, now(), :note)
                    RETURNING id::text
                """),
                {
                    "tid": tenant_id,
                    "docs": json.dumps(snapshots),
                    "n": len(snapshots),
                    "note": f"Approbation automatique : purge de la corbeille après "
                    f"{lifecycle.TRASH_RETENTION_DAYS} jours",
                },
            )
        ).scalar()
        await execute(conn, tenant_id, tenant, record_id, snapshots, "purge")
    await finish(engine, tenant_id, record_id)
    await log_action(
        action="destruction_approved",
        details={
            "destruction_id": record_id,
            "kind": "trash_purge",
            "documents": len(due),
        },
    )
    for s in snapshots:
        await log_action(
            action="document_destroyed",
            table_name=s["table_name"],
            document_id=s["id"],
            details={
                "destruction_id": record_id,
                "record_id": s["record_id"],
                "via": "trash_purge",
            },
        )
    return len(due)


async def finish(engine, tenant_id: str, record_id: str) -> None:
    """After the destruction committed: delete the files on this node and
    write the PV. Neither may undo the destruction - failures are logged
    (files are retried by the sweep, the PV can be regenerated)."""
    try:
        await purge_destroyed_files(engine)
    except Exception as exc:
        print(f"[destruction] WARNING: file deletion failed: {exc}", file=sys.stderr)
    try:
        await write_pv(engine, tenant_id, record_id)
    except Exception as exc:
        print(f"[destruction] WARNING: PV generation failed: {exc}", file=sys.stderr)


# ── File deletion on every node ───────────────────────────────────────────────


async def purge_destroyed_files(engine) -> int:
    """Delete the files listed in sdai_destroyed_files that this node has
    not processed yet (locally destroyed or received from the peer), drop
    them from the sync file queue, and remember them as done. Paths outside
    the sync file roots are never touched. Returns the files deleted."""
    from sync import files as sync_files

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT f.id::text, f.path FROM sdai_destroyed_files f
                    WHERE NOT EXISTS (SELECT 1 FROM sdai_destroyed_files_done d WHERE d.id = f.id)
                    ORDER BY f.destroyed_at LIMIT 5000
                """)
            )
        ).all()
    deleted = 0
    for file_id, path in rows:
        try:
            target = sync_files.resolve(path)
        except sync_files.FileSyncError:
            target = None  # outside the roots: never deleted, just marked done
        if target is not None:
            for p in (target, sync_files.part_of(target)):
                try:
                    p.unlink()
                    deleted += p == target
                except FileNotFoundError:
                    pass
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM sdai_sync_file_queue WHERE path = :p"), {"p": path}
            )
            await conn.execute(
                text(
                    "INSERT INTO sdai_destroyed_files_done (id) VALUES (CAST(:id AS uuid))"
                    " ON CONFLICT DO NOTHING"
                ),
                {"id": file_id},
            )
    return deleted


async def is_destroyed_path(conn, path: str) -> bool:
    return bool(
        (
            await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sdai_destroyed_files WHERE path = :p)"
                ),
                {"p": path},
            )
        ).scalar()
    )


async def heal_tombstones(engine) -> int:
    """Delete version, field-history, tag, document-grant and page rows still
    attached to a destroyed document - they come back when a node restored
    from a snapshot older than the destruction re-sends them (sync reseed).
    Idempotent; the deletes sync like any other. Returns the rows deleted."""
    deleted = 0
    async with engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    text("""
                    SELECT DISTINCT table_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND column_name = 'destruction_id'
                      AND starts_with(table_name, 't_')
                """)
                )
            )
            .scalars()
            .all()
        )
        for table in tables:
            dead = (
                f"""(SELECT id FROM "{table}" WHERE lifecycle_status = 'destroyed')"""
            )
            for statement in (
                f"DELETE FROM sdai_document_versions WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_field_history WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_document_tags WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_document_access WHERE scope = 'document' AND table_name = :t"
                f" AND document_id IN {dead}",
                f"DELETE FROM sdai_comments WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_notifications WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_signature_requests WHERE table_name = :t AND document_id IN {dead}",
                f"DELETE FROM sdai_signatures WHERE table_name = :t AND document_id IN {dead}",
                f"""DELETE FROM batch_document_pages WHERE batch_document_id IN (
                        SELECT batch_document_id FROM "{table}"
                        WHERE lifecycle_status = 'destroyed' AND batch_document_id IS NOT NULL)""",
            ):
                deleted += (
                    await conn.execute(text(statement), {"t": table})
                ).rowcount or 0
    return deleted


async def destroyed_files_worker() -> None:
    """Every node: catch up on destroyed files (rows that arrived through a
    reseed, files restored from a backup, a failed deletion) and on rows
    still attached to tombstones (heal_tombstones)."""
    from ingest_router import _engine

    while True:
        try:
            await purge_destroyed_files(_engine())
            await heal_tombstones(_engine())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"[destruction] WARNING: destroyed-files sweep failed: {exc}",
                file=sys.stderr,
            )
        await asyncio.sleep(DESTROYED_FILES_SWEEP_SECONDS)


# ── Procès-verbal ─────────────────────────────────────────────────────────────

_PV = {
    "fr": {
        "title": "Procès-verbal d'élimination",
        "number": "Numéro",
        "tenant": "Organisme",
        "kind": "Nature",
        "kind_retention": "Élimination à l'échéance de la durée de conservation",
        "kind_trash_purge": "Purge de la corbeille (approbation automatique)",
        "proposed": "Proposé par",
        "approved": "Approuvé par",
        "executed": "Exécuté le",
        "comment": "Motif / commentaire",
        "note": "Note d'approbation",
        "count": "Nombre de documents",
        "auto": "Système (automatique)",
        "intro": (
            "Les documents ci-dessous ont été éliminés du système d'archivage électronique : "
            "leurs fichiers (toutes versions, images de pages, fichiers d'origine) ont été "
            "supprimés sur le serveur local et sur le serveur distant, ainsi que leurs données "
            "extraites. Une notice minimale (cote, type, dates, empreintes SHA-256) est conservée "
            "comme trace de l'élimination."
        ),
        "backup": (
            "Avertissement : les sauvegardes chiffrées (restic) réalisées avant cette date "
            "contiennent encore ces fichiers jusqu'à l'expiration de leur durée de rétention "
            "(30 sauvegardes quotidiennes, 12 mensuelles). Ils n'en sont pas restaurés "
            "automatiquement."
        ),
        "docs": "Documents éliminés",
        "cols": ["Cote", "Type", "Série", "Dates", "Empreintes SHA-256"],
        "ingested": "versé",
        "archived": "archivé",
        "retention": "échéance",
        "trashed": "corbeille",
        "original": "original",
        "version": "v",
        "files": "Fichiers supprimés",
        "files_text": "{n} fichier(s) supprimé(s) sur ce serveur et transmis pour suppression au serveur distant.",
        "signatures": "Signatures",
        "generated": "Document généré le {when} UTC",
    },
    "en": {
        "title": "Destruction certificate",
        "number": "Number",
        "tenant": "Organisation",
        "kind": "Kind",
        "kind_retention": "Destruction at the end of the retention period",
        "kind_trash_purge": "Trash purge (automatic approval)",
        "proposed": "Proposed by",
        "approved": "Approved by",
        "executed": "Executed on",
        "comment": "Reason / comment",
        "note": "Approval note",
        "count": "Number of documents",
        "auto": "System (automatic)",
        "intro": (
            "The documents below were destroyed in the electronic archive: their files (every "
            "version, page images, original files) were deleted on the local and the remote "
            "server, together with their extracted data. A minimal record (reference, type, "
            "dates, SHA-256 fingerprints) is kept as the trace of the destruction."
        ),
        "backup": (
            "Warning: encrypted backups (restic) taken before this date still contain these "
            "files until their retention expires (30 daily, 12 monthly backups). They are not "
            "restored from them automatically."
        ),
        "docs": "Destroyed documents",
        "cols": ["Reference", "Type", "Series", "Dates", "SHA-256 fingerprints"],
        "ingested": "filed",
        "archived": "archived",
        "retention": "retention end",
        "trashed": "trash",
        "original": "original",
        "version": "v",
        "files": "Deleted files",
        "files_text": "{n} file(s) deleted on this server and sent for deletion to the remote server.",
        "signatures": "Signatures",
        "generated": "Generated on {when} UTC",
    },
}


def render_pv(
    record: dict, tenant_name: str, locales: list[str], file_count: int
) -> bytes:
    """The PV as PDF bytes - one part per locale (French first)."""
    from pdf_report import Report

    number = record_number(record["id"], record.get("proposed_at"))
    docs = record["documents"]
    if isinstance(docs, str):
        docs = json.loads(docs)
    report = Report(footer=f"{number} - {tenant_name}")
    for n, locale in enumerate(locales):
        L = _PV[locale]
        if n:
            report.space(30)
        report.heading(f"{L['title']} {number}")
        auto = L["auto"]
        report.fields(
            [
                (L["number"], number),
                (L["tenant"], tenant_name),
                (L["kind"], L[f"kind_{record['kind']}"]),
                (L["count"], str(record["document_count"])),
                (
                    L["proposed"],
                    f"{record.get('pv_proposed_by_email') or record.get('proposed_by_email') or auto} - {format_date(record.get('proposed_at'), locale)}",
                ),
                (
                    L["approved"],
                    f"{record.get('pv_decided_by_email') or record.get('decided_by_email') or auto} - {format_date(record.get('decided_at'), locale)}",
                ),
                (L["executed"], format_datetime(record.get("executed_at"), locale)),
                (L["comment"], record.get("comment") or "-"),
                (L["note"], record.get("decision_note") or "-"),
            ]
        )
        report.paragraph(L["intro"])
        report.paragraph(L["backup"], bold=True)
        report.subheading(L["docs"])
        rows = []
        for d in docs:
            dates = [f"{L['ingested']} {format_date(d.get('ingested_at'), locale)}"]
            if d.get("archived_at"):
                dates.append(f"{L['archived']} {format_date(d['archived_at'], locale)}")
            if d.get("retention_until"):
                dates.append(f"{L['retention']} {format_date(d['retention_until'], locale)}")
            if d.get("trashed_at"):
                dates.append(f"{L['trashed']} {format_date(d['trashed_at'], locale)}")
            # One line per distinct file hash, with every file that had it
            # (the original is usually version 1 too).
            labels: dict[str, list[str]] = {}
            if d.get("original_hash"):
                labels.setdefault(d["original_hash"], []).append(L["original"])
            for v in d.get("versions") or []:
                if v.get("file_hash"):
                    labels.setdefault(v["file_hash"], []).append(
                        f"{L['version']}{v['version_no']}"
                    )
            hashes = [f"{', '.join(names)}: {h}" for h, names in labels.items()]
            rows.append(
                [
                    d.get("record_id") or d["id"],
                    d.get("document_type") or "",
                    d.get("series") or "-",
                    "\n".join(dates),
                    "\n".join(hashes) or "-",
                ]
            )
        report.table(L["cols"], rows, widths=[0.14, 0.13, 0.15, 0.17, 0.41])
        report.subheading(L["files"])
        report.paragraph(L["files_text"].format(n=file_count))
        report.subheading(L["signatures"])
        line = "_" * 40
        report.fields([(L["proposed"], f"\n{line}"), (L["approved"], f"\n{line}")])
        report.paragraph(
            L["generated"].format(
                when=format_datetime(datetime.now(timezone.utc), locale)
            ),
            size=7,
        )
    return report.build()


async def write_pv(engine, tenant_id: str, record_id: str) -> Path:
    """Generate the PV of an executed record into
    DATA_DIR/generated/destruction/<tenant>/<number>.pdf (never overwritten -
    a regeneration gets a suffix) and record its path and hash."""
    async with engine.begin() as conn:
        record = await load_record(conn, tenant_id, record_id)
        tenant = (
            (
                await conn.execute(
                    text(
                        "SELECT slug, name, locale FROM sdai_tenants WHERE id = CAST(:tid AS uuid)"
                    ),
                    {"tid": tenant_id},
                )
            )
            .mappings()
            .one()
        )
        file_count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM sdai_destroyed_files WHERE destruction_id = CAST(:id AS uuid)"
                ),
                {"id": record_id},
            )
        ).scalar()
        locales = ["fr"] + (["en"] if (tenant["locale"] or "").startswith("en") else [])
        data = await asyncio.to_thread(
            render_pv, record, tenant["name"], locales, file_count
        )
        folder = pv_dir(tenant["slug"]).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        number = record_number(record_id, record["proposed_at"])
        path = folder / f"{number}.pdf"
        n = 1
        while path.exists():
            n += 1
            path = folder / f"{number}_{n}.pdf"
        with open(path, "xb") as fh:
            fh.write(data)
        await conn.execute(
            text(
                "UPDATE sdai_destruction_records SET file_path = :p, file_hash = :h"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"p": str(path), "h": hashlib.sha256(data).hexdigest(), "id": record_id},
        )
    return path
