"""Changelog reads, peer cursors and the apply transaction shared by the
sync API (the serving node) and the worker (the initiating node)."""

from __future__ import annotations

import sys

from cache import invalidate_cache
from sqlalchemy import text

from sync.apply import ApplyResult, apply_changes

# Every Redis-cached area whose content can come from a synced row: schema
# and types (new per-type tables/columns), review counts and reviewers.
_CACHE_NAMESPACES = ("schema", "types", "review", "reviewers")

_CHANGE_COLS = (
    "id, table_name, pk, op, row_data, base_hlcs, hlc, origin_node, created_at"
)


async def read_changes(conn, since: int, limit: int) -> list[dict]:
    rows = await conn.execute(
        text(
            f"SELECT {_CHANGE_COLS} FROM sdai_sync_changelog WHERE id > :s ORDER BY id LIMIT :l"
        ),
        {"s": since, "l": limit},
    )
    changes = []
    for r in rows.mappings().all():
        change = dict(r)
        change["created_at"] = (
            change["created_at"].isoformat() if change["created_at"] else None
        )
        changes.append(change)
    return changes


async def local_epoch(conn) -> str:
    return str(
        (
            await conn.execute(text("SELECT epoch FROM sdai_sync_state WHERE id = 1"))
        ).scalar()
    )


async def last_change_id(conn) -> int:
    return (
        await conn.execute(text("SELECT COALESCE(max(id), 0) FROM sdai_sync_changelog"))
    ).scalar()


async def cursor(conn, peer: str, for_update: bool = False) -> dict:
    await conn.execute(
        text(
            "INSERT INTO sdai_sync_cursors (peer_node) VALUES (:p) ON CONFLICT (peer_node) DO NOTHING"
        ),
        {"p": peer},
    )
    lock = " FOR UPDATE" if for_update else ""
    row = (
        (
            await conn.execute(
                text(f"SELECT * FROM sdai_sync_cursors WHERE peer_node = :p{lock}"),
                {"p": peer},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def mark_sent(conn, peer: str, sent_id: int) -> None:
    """The peer has applied our changelog up to sent_id - as the peer
    reports it (a push ack, the `since` it pulls from), so it can move
    back when the peer was restored or joined again."""
    await cursor(conn, peer)
    await conn.execute(
        text("""
            UPDATE sdai_sync_cursors
            SET sent_id = :s, last_sent_at = now(), last_contact_at = now()
            WHERE peer_node = :p
        """),
        {"p": peer, "s": sent_id},
    )


def _change_id(change) -> int:
    value = change.get("id") if isinstance(change, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def apply_from_peer(
    conn, peer: str, epoch: str, changes: list[dict]
) -> tuple[ApplyResult | None, int]:
    """Apply a page of the peer's changes exactly once, in the caller's
    transaction: serialized per node (advisory lock), changes at or below
    the peer's received_id are skipped (a retried push or pull), and
    received_id moves to the last change of the page. Returns the result
    and the new received_id.

    When the peer's changelog epoch is not the one this cursor was built
    on (it was restored or joined again), nothing is applied: the cursor
    restarts at 0 under the new epoch and (None, 0) tells the caller to
    resend from the beginning - a page from the middle of an unknown
    changelog can't be trusted to be complete."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('sdai_sync_apply'))")
    )
    current = await cursor(conn, peer, for_update=True)
    received = current["received_id"]
    if current["peer_epoch"] is not None and str(current["peer_epoch"]) != str(epoch):
        await conn.execute(
            text(
                "UPDATE sdai_sync_cursors SET received_id = 0, peer_epoch = CAST(:e AS uuid),"
                " last_contact_at = now() WHERE peer_node = :p"
            ),
            {"p": peer, "e": str(epoch)},
        )
        return None, 0
    fresh = [c for c in changes if _change_id(c) > received]
    result = await apply_changes(conn, fresh)
    received = max([received, *(_change_id(c) for c in fresh)])
    await conn.execute(
        text("""
            UPDATE sdai_sync_cursors
            SET received_id = :r, peer_epoch = CAST(:e AS uuid),
                last_received_at = now(), last_contact_at = now()
            WHERE peer_node = :p
        """),
        {"p": peer, "r": received, "e": str(epoch)},
    )
    if "sdai_series" in result.tables:
        from classification_router import repair_series_paths

        # Paths follow parent_id; concurrent moves on both nodes may even
        # have formed a cycle - fixed identically on both sides.
        await repair_series_paths(conn)
    if any(t and t.startswith("t_") for t in result.tables):
        import ingest_router

        # Documents numbered before a restore of this node came back.
        await ingest_router._repair_record_counters(conn)
    # A joined node waits for the peer's tenants before startup may create
    # its own 'default' again (sync.join).
    await conn.execute(
        text(
            "UPDATE sdai_sync_state SET join_pending = false"
            " WHERE join_pending AND EXISTS (SELECT 1 FROM sdai_tenants)"
        )
    )
    from sync.conflicts import record_outcome

    await record_outcome(conn, peer, result)
    return result, received


# ── Serving side (the peer's worker calls these through sync.api) ─────────────


async def serve_hello(conn, peer: str) -> dict:
    import ingest_router

    theirs = await cursor(conn, peer)
    return {
        "node_id": ingest_router.NODE_ID,
        "node_role": ingest_router.NODE_ROLE,
        "epoch": await local_epoch(conn),
        "head": await last_change_id(conn),
        # The caller resumes pushing from here (it may have been reset by a
        # restore or join on this side).
        "received_id": theirs["received_id"],
        "received_epoch": str(theirs["peer_epoch"]) if theirs["peer_epoch"] else None,
    }


async def serve_push(
    conn, peer: str, epoch: str, changes: list
) -> tuple[dict, ApplyResult | None]:
    import ingest_router

    result, received = await apply_from_peer(conn, peer, epoch, changes)
    if result is None:
        # The caller's cursor was for an older changelog of ours: start over.
        return {"node_id": ingest_router.NODE_ID, "ack": 0, "epoch_reset": True}, None
    return {
        "node_id": ingest_router.NODE_ID,
        "ack": received,
        "epoch_reset": False,
        "applied": result.applied,
        "skipped": result.skipped,
        "conflicts": len(result.conflicts),
        "errors": len(result.errors),
    }, result


async def history_diverged(
    conn, peer: str, peer_epoch: str | None, peer_has: int
) -> str | None:
    """Why the peer's idea of how much of our changelog it holds no longer
    fits ours - one side was restored from a backup or rebuilt - or None:

      we_went_back     same epoch, it holds more than we have: our new
                       changes would reuse ids it thinks it has
      peer_went_back   same epoch, it holds less than it acknowledged
      peer_rebuilt     other/no epoch, though we had sent it some

    The answer is always a reseed - a full snapshot of every row, merged on
    the other side field by field on the real stamps (nothing newer is
    overwritten). A peer that went back also lost rows of its own that only
    we hold now (our changelog carries only our changes) - the snapshot
    has them. When WE went back, we lost rows only the peer holds: the
    caller also asks the peer for its snapshot (forget_peer)."""
    epoch = await local_epoch(conn)
    sent = (await cursor(conn, peer))["sent_id"]
    if peer_epoch == epoch:
        if peer_has > await last_change_id(conn):
            return "we_went_back"
        return "peer_went_back" if peer_has < sent else None
    return "peer_rebuilt" if sent > 0 else None


async def forget_peer(conn, peer: str) -> None:
    """Pull the peer's changelog as if we had never seen it: it sees an
    unknown epoch from a node it had sent changes to (peer_rebuilt on its
    side) and answers with a full snapshot."""
    await cursor(conn, peer)
    await conn.execute(
        text(
            "UPDATE sdai_sync_cursors SET received_id = 0, peer_epoch = NULL WHERE peer_node = :p"
        ),
        {"p": peer},
    )


async def reseed_for(conn, peer: str, reason: str) -> None:
    from sync.capture import seed_changelog

    print(f"[sync] reseeding the changelog for {peer}: {reason}", file=sys.stderr)
    await seed_changelog(conn, force=True)


async def ensure_history_from(conn, since: int) -> bool:
    """A peer wants our changelog after `since`, but rows after it were
    pruned: reseed (new epoch) so it starts over from a full snapshot
    instead of silently missing changes. True when it reseeded."""
    from sync.capture import seed_changelog

    pruned = (
        await conn.execute(
            text("SELECT pruned_through FROM sdai_sync_state WHERE id = 1")
        )
    ).scalar()
    if since >= pruned:
        return False
    await seed_changelog(conn, force=True)
    return True


async def prune_changelog(conn, retention_days: float) -> int:
    """Delete changelog rows every known peer has applied and that are older
    than the retention. Nothing goes while no peer has ever synced (a node
    joining later needs the whole changelog). Returns rows deleted."""
    floor = (
        await conn.execute(text("SELECT min(sent_id) FROM sdai_sync_cursors"))
    ).scalar()
    if not floor:
        return 0
    deleted = await conn.execute(
        text("""
            DELETE FROM sdai_sync_changelog
            WHERE id <= :f AND created_at < now() - make_interval(secs => :secs)
            RETURNING id
        """),
        {"f": floor, "secs": retention_days * 86400},
    )
    ids = [r[0] for r in deleted]
    if ids:
        await conn.execute(
            text(
                "UPDATE sdai_sync_state SET pruned_through = GREATEST(pruned_through, :m) WHERE id = 1"
            ),
            {"m": max(ids)},
        )
    return len(ids)


async def serve_pull(
    conn, peer: str, since: int, limit: int, peer_epoch: str | None = None
) -> dict:
    """`peer_epoch`: the epoch of our changelog the peer's `since` refers to
    (None for a peer that never got anything from us)."""
    import ingest_router

    reason = await history_diverged(conn, peer, peer_epoch, since)
    if reason:
        await reseed_for(conn, peer, reason)
        since = 0
    elif await ensure_history_from(conn, since):
        since = 0
    # Asking from `since` of our current epoch means the peer has applied
    # everything up to it. A `since` of another epoch means nothing here:
    # the peer discards this page and starts over from 0 (also right after
    # a reseed above).
    current = peer_epoch == await local_epoch(conn)
    await mark_sent(conn, peer, since if current else 0)
    changes = await read_changes(conn, since, limit + 1)
    has_more = len(changes) > limit
    changes = changes[:limit]
    return {
        "node_id": ingest_router.NODE_ID,
        "changes": changes,
        "last_id": changes[-1]["id"] if changes else since,
        "has_more": has_more,
        "head": await last_change_id(conn),
        "epoch": await local_epoch(conn),
    }


async def after_apply(result: ApplyResult | None, peer: str | None = None) -> None:
    """Outside the transaction, once committed: drop caches the applied rows
    may have made stale (shared through Redis with the API workers), and
    audit the conflicts and failed changes."""
    if result and (result.applied or result.conflicts or result.superseded):
        await invalidate_cache(*_CACHE_NAMESPACES)
    if result and (result.conflicts or result.errors):
        from sync.conflicts import audit_outcome

        await audit_outcome(result, peer or "")
