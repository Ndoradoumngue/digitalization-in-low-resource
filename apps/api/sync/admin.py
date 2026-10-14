"""Admin side of local/cloud sync (/api/admin/sync/*) - user-authenticated,
admin only, unlike the node-to-node routes in sync.api.

  GET  /api/admin/sync/status                          node, worker, peers, pending
       rows/files, conflicts, last backups, alerts
  GET  /api/admin/sync/conflicts?status=open|resolved   list (this tenant's
       conflicts plus deployment-wide ones that belong to no tenant)
  POST /api/admin/sync/conflicts/{id}/resolve           {action, value?}
"""

from __future__ import annotations

import json
import uuid

from audit import client_ip, log_action
from auth import CurrentUser, require_admin
from cache import invalidate_cache
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from i18n import t
from pydantic import BaseModel
from sqlalchemy import text

from sync.conflicts import RESOLUTIONS, ResolveError, resolve

router = APIRouter(prefix="/api/admin/sync", tags=["sync-admin"])

_CONFLICT_COLS = (
    "id::text AS id, kind, status, tenant_id::text AS tenant_id, table_name, pk, document_id::text AS document_id,"
    " field, local_value, remote_value, local_hlc, remote_hlc, remote_node, winner, message, occurrences,"
    " resolution, resolved_by::text AS resolved_by, resolved_at, created_at, updated_at"
)
# Conflicts a tenant admin may see: their own tenant's, and rows that belong
# to no tenant (e.g. a group membership whose group is gone).
_TENANT_SCOPE = "(tenant_id = CAST(:tid AS uuid) OR tenant_id IS NULL)"


def _engine():
    import ingest_router

    return ingest_router._engine()


def _serial(row: dict) -> dict:
    return {
        k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()
    }


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _pending_file_bytes(conn) -> int:
    """Size of the files still to transfer, as far as this node can tell:
    files it has to push are on its disk (bounded to the first 2000)."""
    from sync import files

    rows = (
        await conn.execute(
            text(
                "SELECT path, direction, size FROM sdai_sync_file_queue"
                " WHERE status IN ('pending', 'waiting') ORDER BY created_at LIMIT 2000"
            )
        )
    ).all()
    total = 0
    for path, direction, size in rows:
        if size:
            total += size
        elif direction == "push":
            try:
                total += files.local_path(path).stat().st_size
            except OSError:
                pass
    return total


def node_alerts(state: dict, peers: list[dict], backups: list[dict], last_backup_success, now) -> list[dict]:
    """Alerts about this node itself: sync down for longer than
    SYNC_ALERT_HOURS, backups missing / failed / stale."""
    from sync import config

    initiator = bool(config.SYNC_PEER_URL)
    alerts = []
    if config.enabled():
        # The local node knows from its own worker; the passive cloud node
        # from when the local one last reached it.
        last_ok = (
            state["last_success_at"]
            if initiator
            else max(
                (p["last_contact_at"] for p in peers if p["last_contact_at"]),
                default=None,
            )
        )
        reference = last_ok or state["seeded_at"]
        if (
            reference is None
            or (now - reference).total_seconds() > config.SYNC_ALERT_HOURS * 3600
        ):
            alerts.append(
                {
                    "kind": "sync_down",
                    "since": _iso(last_ok),
                    "hours": config.SYNC_ALERT_HOURS,
                }
            )
    latest = max(backups, key=lambda b: b["finished_at"], default=None)
    if latest is None or latest["status"] == "not_configured":
        alerts.append({"kind": "backup_not_configured"})
    elif any(b["status"] == "failed" for b in backups):
        alerts.append({"kind": "backup_failed", "since": _iso(last_backup_success)})
    elif (now - latest["finished_at"]).total_seconds() > 36 * 3600:
        alerts.append({"kind": "backup_stale", "since": _iso(latest["finished_at"])})
    return alerts


async def compute_alerts(conn) -> list[dict]:
    """node_alerts from the database - used by the admin notifications
    (notifications.check_ops_alerts)."""
    state = dict(
        (
            await conn.execute(
                text("SELECT seeded_at, last_success_at FROM sdai_sync_state WHERE id = 1")
            )
        )
        .mappings()
        .one_or_none()
        or {"seeded_at": None, "last_success_at": None}
    )
    peers = [
        dict(r)
        for r in (
            await conn.execute(text("SELECT last_contact_at FROM sdai_sync_cursors"))
        ).mappings()
    ]
    backups = [
        dict(r)
        for r in (
            await conn.execute(
                text("""
                    SELECT DISTINCT ON (COALESCE(target, '-')) target, status, finished_at
                    FROM sdai_backup_runs ORDER BY COALESCE(target, '-'), finished_at DESC
                """)
            )
        ).mappings()
    ]
    last_backup_success = (
        await conn.execute(
            text("SELECT max(finished_at) FROM sdai_backup_runs WHERE status = 'success'")
        )
    ).scalar()
    now = (await conn.execute(text("SELECT now()"))).scalar()
    return node_alerts(state, peers, backups, last_backup_success, now)


@router.get("/status", summary="Sync and backup status of this node")
async def sync_status(current_user: CurrentUser = Depends(require_admin)):
    import ingest_router

    from sync import config

    async with _engine().connect() as conn:
        state = dict(
            (
                await conn.execute(
                    text(
                        "SELECT epoch::text AS epoch, seeded_at, last_cycle_at, last_success_at,"
                        " last_error, last_error_at, failures, join_pending FROM sdai_sync_state WHERE id = 1"
                    )
                )
            )
            .mappings()
            .one()
        )
        head = (
            await conn.execute(
                text("SELECT COALESCE(max(id), 0) FROM sdai_sync_changelog")
            )
        ).scalar()
        peers = [
            dict(r)
            for r in (
                await conn.execute(
                    text(
                        "SELECT peer_node, received_id, sent_id, last_received_at, last_sent_at,"
                        " last_contact_at, last_success_at, last_summary FROM sdai_sync_cursors"
                        " ORDER BY peer_node"
                    )
                )
            ).mappings()
        ]
        for peer in peers:
            peer["pending_changes"] = max(0, head - peer["sent_id"])
        files_by_status = {
            r[0]: r[1]
            for r in await conn.execute(
                text(
                    "SELECT status, count(*) FROM sdai_sync_file_queue GROUP BY status"
                )
            )
        }
        pending_bytes = await _pending_file_bytes(conn)
        conflicts_open = (
            await conn.execute(
                text(
                    f"SELECT count(*) FROM sdai_sync_conflicts WHERE status = 'open' AND {_TENANT_SCOPE}"
                ),
                {"tid": current_user.tenant_id},
            )
        ).scalar()
        backups = [
            dict(r)
            for r in (
                await conn.execute(
                    text("""
                        SELECT DISTINCT ON (COALESCE(target, '-')) target, status, started_at, finished_at,
                               snapshot_id, bytes_added, message
                        FROM sdai_backup_runs ORDER BY COALESCE(target, '-'), finished_at DESC
                    """)
                )
            ).mappings()
        ]
        last_backup_success = (
            await conn.execute(
                text(
                    "SELECT max(finished_at) FROM sdai_backup_runs WHERE status = 'success'"
                )
            )
        ).scalar()
        now = (await conn.execute(text("SELECT now()"))).scalar()
        # E-mails this node gave up sending (GED Phase 7, notifications_router).
        from notifications_router import failed_email_count

        emails_failed = await failed_email_count(conn, current_user.tenant_id)

    initiator = bool(config.SYNC_PEER_URL)
    alerts = node_alerts(state, peers, backups, last_backup_success, now)
    if files_by_status.get("failed"):
        alerts.append({"kind": "files_failed", "count": files_by_status["failed"]})
    if conflicts_open:
        alerts.append({"kind": "conflicts_open", "count": conflicts_open})
    if emails_failed:
        alerts.append({"kind": "emails_failed", "count": emails_failed})

    return {
        "node": {
            "node_id": ingest_router.NODE_ID,
            "node_role": ingest_router.NODE_ROLE,
            "sync_enabled": config.enabled(),
            "initiator": initiator,
            "peer_url": config.SYNC_PEER_URL or None,
            "join_pending": state["join_pending"],
        },
        "worker": {
            k: _iso(state[k])
            for k in ("last_cycle_at", "last_success_at", "last_error_at")
        }
        | {"last_error": state["last_error"], "failures": state["failures"]},
        "changelog": {
            "head": head,
            "epoch": state["epoch"],
            "seeded_at": _iso(state["seeded_at"]),
        },
        "peers": [{k: _iso(v) for k, v in p.items()} for p in peers],
        "files": {
            "pending": files_by_status.get("pending", 0),
            "waiting": files_by_status.get("waiting", 0),
            "failed": files_by_status.get("failed", 0),
            "done": files_by_status.get("done", 0),
            "pending_bytes": pending_bytes,
        },
        "conflicts_open": conflicts_open,
        "backups": [{k: _iso(v) for k, v in b.items()} for b in backups],
        "last_backup_success": _iso(last_backup_success),
        "alerts": alerts,
    }


@router.get("/files", summary="Files that failed or are still waiting to sync")
async def problem_files(current_user: CurrentUser = Depends(require_admin)):
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT path, direction, status, table_name, attempts, last_error, next_attempt_at, updated_at
                FROM sdai_sync_file_queue WHERE status IN ('failed', 'waiting')
                ORDER BY status, updated_at DESC LIMIT 200
            """)
        )
        return {
            "items": [{k: _iso(v) for k, v in dict(r).items()} for r in rows.mappings()]
        }


@router.post("/files/retry", summary="Retry every failed file transfer")
async def retry_files(
    request: Request, current_user: CurrentUser = Depends(require_admin)
):
    async with _engine().begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE sdai_sync_file_queue SET status = 'pending', attempts = 0, last_error = NULL,"
                " next_attempt_at = now(), updated_at = now() WHERE status IN ('failed', 'waiting')"
            )
        )
    await log_action(
        action="sync_files_retried",
        user_id=current_user.id,
        user_email=current_user.email,
        details={"count": result.rowcount},
        ip_address=client_ip(request),
    )
    return {"retried": result.rowcount}


@router.get("/conflicts", summary="Sync conflicts of this tenant")
async def list_conflicts(
    status: str = Query("open", pattern="^(open|resolved)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_admin),
):
    async with _engine().connect() as conn:
        rows = await conn.execute(
            text(f"""
                SELECT {_CONFLICT_COLS}, COUNT(*) OVER () AS total_count
                FROM sdai_sync_conflicts
                WHERE status = :s AND {_TENANT_SCOPE}
                ORDER BY created_at DESC
                LIMIT :l OFFSET :o
            """),
            {
                "s": status,
                "tid": current_user.tenant_id,
                "l": page_size,
                "o": (page - 1) * page_size,
            },
        )
        items = [dict(r) for r in rows.mappings().all()]
    total = int(items[0]["total_count"]) if items else 0
    return {
        "total": total,
        "items": [
            _serial({k: v for k, v in r.items() if k != "total_count"}) for r in items
        ],
    }


class ResolveBody(BaseModel):
    action: str
    value: object | None = None


@router.post("/conflicts/{conflict_id}/resolve", summary="Resolve a sync conflict")
async def resolve_conflict(
    conflict_id: str,
    body: ResolveBody,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
):
    locale = current_user.locale
    try:
        uuid.UUID(conflict_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=t("sync.conflict_not_found", locale)
        ) from exc
    if body.action not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail=t("sync.bad_resolution", locale))
    if body.action == "manual" and body.value is None:
        raise HTTPException(status_code=400, detail=t("sync.value_required", locale))

    async with _engine().begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        f"SELECT {_CONFLICT_COLS}, change FROM sdai_sync_conflicts"
                        f" WHERE id = CAST(:id AS uuid) AND {_TENANT_SCOPE} FOR UPDATE"
                    ),
                    {"id": conflict_id, "tid": current_user.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=t("sync.conflict_not_found", locale)
            )
        conflict = dict(row)
        try:
            outcome = await resolve(
                conn, conflict, body.action, body.value, current_user.id
            )
        except ResolveError as exc:
            raise HTTPException(
                status_code=exc.status, detail=t(exc.key, locale)
            ) from exc

    # The document leaves the review queue's "sync conflict" list, and a
    # written value may change counts.
    await invalidate_cache("review")
    await log_action(
        action="sync_conflict_resolved",
        user_id=current_user.id,
        user_email=current_user.email,
        table_name=conflict["table_name"] if conflict["document_id"] else None,
        document_id=conflict["document_id"],
        details={
            "conflict_id": conflict_id,
            "kind": conflict["kind"],
            "field": conflict["field"],
            "resolution": body.action,
            "value": json.loads(json.dumps(body.value, default=str))
            if body.action == "manual"
            else None,
        },
        ip_address=client_ip(request),
    )
    return {"id": conflict_id, "status": "resolved", **outcome}
