"""Prepare a freshly installed node to receive a whole deployment from its
peer (e.g. the new cloud node). Run once, before its first sync:

    docker compose exec api python -m sync.join

A fresh database seeds its own 'default' tenant (with its own id) - it
would collide with the peer's. Joining removes it and resets this node's
sync bookkeeping (new changelog epoch), so the next sync sends it the peer's
tenants, users, documents and audit log in unchanged (join_pending keeps
startup from re-creating a 'default' tenant meanwhile; the first applied
page that brings tenants clears it). Refuses to run on a
database that already holds users, uploads or documents.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from sync.capture import dynamic_tables


class NotEmpty(RuntimeError):
    pass


async def prepare_fresh_node(conn) -> None:
    for table in ("sdai_users", "sdai_api_keys", "batches", "sdai_audit_log"):
        if (
            await conn.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})"))
        ).scalar():
            raise NotEmpty(
                f"This database is not empty ({table} has rows) - refusing to join."
            )
    if await dynamic_tables(conn):
        raise NotEmpty("This database already has document tables - refusing to join.")
    await conn.execute(text("SET LOCAL sdai.applying_sync = on"))
    await conn.execute(text("DELETE FROM sdai_prompt_presets"))
    await conn.execute(text("DELETE FROM sdai_record_id_counters"))
    await conn.execute(text("DELETE FROM sdai_tenants"))
    await conn.execute(
        text("TRUNCATE sdai_sync_changelog, sdai_sync_row_clock, sdai_sync_cursors")
    )
    await conn.execute(
        text(
            "UPDATE sdai_sync_state SET seeded_at = now(), epoch = gen_random_uuid(),"
            " join_pending = true WHERE id = 1"
        )
    )


async def _main() -> int:
    from sync.db import open_engine

    engine = await open_engine()
    try:
        async with engine.begin() as conn:
            await prepare_fresh_node(conn)
    except NotEmpty as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print("Ready: the next sync brings the whole deployment from the peer.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
