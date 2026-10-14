"""Change capture for local/cloud sync (GED Phase 8.1).

Every write to a synced table is recorded by an AFTER trigger
(sdai_capture_change) in sdai_sync_changelog, in commit-independent id
order, which the sync worker ships to the peer node. Nothing in the
application has to remember to log a change.

  sdai_sync_changelog   one row per captured change: table, primary key
                        (JSONB), op insert/update/delete/ddl, the data
                        (whole row for an insert, only the changed columns
                        for an update, the column list for a ddl event),
                        base_hlcs, HLC stamp and origin node.
  sdai_sync_row_clock   per synced row, the HLC of the last change to each
                        field ("*" = the insert, for fields untouched since).
                        An update records in base_hlcs which clock values
                        it overwrote - the receiving node compares them with
                        its own clock to tell a fast-forward from a
                        concurrent edit of the same field (conflict, 8.4).
  sdai_sync_state       node-local: HLC floor (raised past every remote
                        stamp applied - sync.hlc), when the changelog was
                        seeded, its epoch (new whenever the changelog
                        restarts - seed or join - so a peer holding a cursor
                        into the old one starts over instead of skipping
                        changes) and join_pending (sync.join: no 'default'
                        tenant is created until the peer's data arrived).

Changes applied BY the sync worker run with `SET LOCAL sdai.applying_sync
= on`: the triggers skip them, so a change never echoes back to where it
came from. Node-local tables (sdai_nodes, sdai_record_id_counters,
sdai_audit_chain_heads, the sdai_sync_* tables) are never captured.

Dynamic per-type tables (t_<tenant>_<type>) get the trigger from
ingest_router._ensure_table, and their creation / new columns are ddl
events (sdai_log_ddl) so the peer creates the table before applying rows.

DDL here follows ingest_router's rules: one statement per string (asyncpg
can't run several), idempotent. It runs BEFORE ingest_router._INIT_DDL,
which reads sdai_sync_state.join_pending - nothing here may depend on the
tables created there (the functions only reference them at run time).
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import text


class SyncTable(NamedTuple):
    name: str
    pk: tuple[str, ...] = ("id",)
    # Append-only tables (audit log, token blocklist) only ever see inserts
    # and never conflict - no row clock needed.
    insert_only: bool = False
    # Seeding order inside the table (rows referencing earlier rows of the
    # same table, or a chain that must stay in order).
    order_by: str | None = None


# Static tables that are replicated, in foreign-key order - the order the
# changelog is seeded in, so a peer can apply it front to back.
SYNC_TABLES: tuple[SyncTable, ...] = (
    SyncTable("sdai_tenants", order_by="created_at"),
    SyncTable("sdai_prompt_presets", order_by="created_at"),
    SyncTable("sdai_users", order_by="created_at"),
    SyncTable("sdai_api_keys", order_by="created_at"),
    SyncTable("sdai_groups", order_by="created_at"),
    SyncTable("sdai_user_groups", pk=("user_id", "group_id")),
    # Parents before children, whenever each was created (a series can be
    # moved under a younger one).
    SyncTable("sdai_series", order_by="length(path) - length(replace(path, '/', '')), created_at"),
    SyncTable("sdai_tags", order_by="created_at"),
    SyncTable("sdai_type_series_defaults", order_by="created_at"),
    SyncTable("sdai_document_access", order_by="granted_at"),
    SyncTable("batches", order_by="created_at"),
    SyncTable("batch_documents", order_by="ingested_at"),
    SyncTable("batch_document_pages", order_by="batch_document_id, page_number"),
    # (dynamic per-type tables are seeded here, after the batches they point at)
    SyncTable("sdai_document_links", order_by="created_at"),
    SyncTable("sdai_document_tags", order_by="created_at"),
    SyncTable("sdai_document_versions", order_by="created_at, version_no"),
    SyncTable("sdai_field_history", order_by="created_at"),
    SyncTable("sdai_audit_log", insert_only=True, order_by="origin_node, chain_seq"),
    SyncTable("sdai_token_blocklist", pk=("jti",), insert_only=True),
)
_DYNAMIC_AFTER = "batch_document_pages"

SYNC_TABLE_BY_NAME = {t.name: t for t in SYNC_TABLES}

# Columns that describe the change rather than the data - never compared,
# never part of an update's changed set.
SYNC_META_COLS = frozenset({"updated_at", "origin_node", "hlc"})

# Node-local tables - documented here so nobody adds them to SYNC_TABLES:
# sdai_nodes (who ran this database), sdai_record_id_counters (record ids are
# only ever assigned on the local node, so the counter can't fork - 8.6),
# sdai_audit_chain_heads (last head verified on THIS node), sdai_sync_*.
NODE_LOCAL_TABLES = frozenset(
    {
        "sdai_nodes",
        "sdai_record_id_counters",
        "sdai_audit_chain_heads",
        "sdai_sync_changelog",
        "sdai_sync_row_clock",
        "sdai_sync_state",
        "sdai_sync_cursors",
        "sdai_sync_conflicts",
        "sdai_sync_file_queue",
        "sdai_backup_runs",
    }
)


SYNC_DDL: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS sdai_sync_changelog (
    id          BIGSERIAL   PRIMARY KEY,
    table_name  TEXT        NOT NULL,
    pk          JSONB       NOT NULL,
    op          TEXT        NOT NULL CHECK (op IN ('insert', 'update', 'delete', 'ddl')),
    row_data    JSONB,
    base_hlcs   JSONB,
    hlc         TEXT        NOT NULL,
    origin_node TEXT        NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
)
""",
    "CREATE INDEX IF NOT EXISTS idx_sdai_sync_changelog_created_at ON sdai_sync_changelog (created_at)",
    """
CREATE TABLE IF NOT EXISTS sdai_sync_row_clock (
    table_name TEXT  NOT NULL,
    pk         TEXT  NOT NULL,
    field_hlcs JSONB NOT NULL,
    PRIMARY KEY (table_name, pk)
)
""",
    """
CREATE TABLE IF NOT EXISTS sdai_sync_state (
    id           INTEGER     PRIMARY KEY CHECK (id = 1),
    hlc_floor_ms BIGINT      NOT NULL DEFAULT 0,
    seeded_at    TIMESTAMPTZ,
    epoch        UUID        NOT NULL DEFAULT gen_random_uuid(),
    join_pending BOOLEAN     NOT NULL DEFAULT false,
    last_cycle_at   TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error      TEXT,
    last_error_at   TIMESTAMPTZ,
    failures        INTEGER     NOT NULL DEFAULT 0,
    pruned_through  BIGINT      NOT NULL DEFAULT 0
)
""",
    "INSERT INTO sdai_sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING",
    # Node-local progress with each peer. received_id: the peer's last
    # changelog id applied here (pushed to us or pulled by us) - pushes at or
    # below it are already applied (idempotent retries). sent_id: our last
    # changelog id the peer has applied (push ack, or the `since` it pulls
    # from) - changelog rows at or below every peer's sent_id can be pruned.
    """
CREATE TABLE IF NOT EXISTS sdai_sync_cursors (
    peer_node        TEXT        PRIMARY KEY,
    peer_epoch       UUID,
    received_id      BIGINT      NOT NULL DEFAULT 0,
    sent_id          BIGINT      NOT NULL DEFAULT 0,
    last_received_at TIMESTAMPTZ,
    last_sent_at     TIMESTAMPTZ,
    last_contact_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    last_error       TEXT,
    failures         INTEGER     NOT NULL DEFAULT 0,
    last_summary     JSONB
)
""",
    "CREATE SEQUENCE IF NOT EXISTS sdai_hlc_counter MINVALUE 0 MAXVALUE 99999 START 0 CYCLE",
    # Node-local record of what an apply could not settle on its own (8.4):
    # kind 'field' = the same field changed on both nodes independently
    # (last writer won, both values kept here until an admin confirms or
    # overrides), any other kind = a change that could not be applied
    # (unique_violation, missing_parent, unknown_table, ...) with the change
    # itself, retryable. Each node keeps its own list - a resolution is an
    # ordinary edit, which reaches the peer and closes its copy (superseded).
    """
CREATE TABLE IF NOT EXISTS sdai_sync_conflicts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    tenant_id    UUID,
    table_name   TEXT,
    pk           JSONB,
    document_id  UUID,
    field        TEXT,
    local_value  JSONB,
    remote_value JSONB,
    local_hlc    TEXT,
    remote_hlc   TEXT,
    remote_node  TEXT,
    winner       TEXT,
    change       JSONB,
    message      TEXT,
    occurrences  INTEGER     NOT NULL DEFAULT 1,
    resolution   TEXT,
    resolved_by  UUID,
    resolved_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now(),
    updated_at   TIMESTAMPTZ DEFAULT now()
)
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS idx_sdai_sync_conflicts_open_field
    ON sdai_sync_conflicts (table_name, (pk::text), field) WHERE status = 'open' AND kind = 'field'
""",
    """
CREATE INDEX IF NOT EXISTS idx_sdai_sync_conflicts_document
    ON sdai_sync_conflicts (table_name, document_id) WHERE status = 'open'
""",
    "CREATE INDEX IF NOT EXISTS idx_sdai_sync_conflicts_status ON sdai_sync_conflicts (status, created_at DESC)",
    # Node-local file transfer queue (8.5, see sync.files): one row per file
    # path and direction - 'push' = this node has it and sends it, 'fetch' =
    # the peer has it and this node downloads it. 'waiting' = not on the
    # source yet (retried with backoff until next_attempt_at), 'failed' =
    # gave up (bad path, different file already there, too many attempts).
    """
CREATE TABLE IF NOT EXISTS sdai_sync_file_queue (
    path            TEXT        NOT NULL,
    direction       TEXT        NOT NULL CHECK (direction IN ('push', 'fetch')),
    status          TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'waiting', 'done', 'failed')),
    table_name      TEXT,
    size            BIGINT,
    sha256          TEXT,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    done_at         TIMESTAMPTZ,
    PRIMARY KEY (path, direction)
)
""",
    """
CREATE INDEX IF NOT EXISTS idx_sdai_sync_file_queue_todo
    ON sdai_sync_file_queue (next_attempt_at) WHERE status IN ('pending', 'waiting')
""",
    # Node-local log of the backup service's runs (scripts/backup/
    # sdai-backup.sh writes one row per target and run) - read by the admin
    # Synchronisation page, which alerts when the last one failed.
    """
CREATE TABLE IF NOT EXISTS sdai_backup_runs (
    id          BIGSERIAL   PRIMARY KEY,
    node_id     TEXT        NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT        NOT NULL CHECK (status IN ('success', 'failed', 'not_configured')),
    target      TEXT,
    snapshot_id TEXT,
    bytes_added BIGINT,
    message     TEXT
)
""",
    "CREATE INDEX IF NOT EXISTS idx_sdai_backup_runs_finished ON sdai_backup_runs (finished_at DESC)",
    # Local HLC stamp - see sync.hlc for the format. Lock-free: the counter
    # is a sequence (never blocks, never rolls back), the floor a single row
    # only the sync applier raises. The counter wrapping inside one
    # millisecond (100 000 changes) is the only way two stamps of one node
    # can sort out of order - not reachable in practice.
    r"""
CREATE OR REPLACE FUNCTION sdai_hlc_now() RETURNS TEXT AS $$
    SELECT lpad(GREATEST(
               floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint,
               COALESCE((SELECT hlc_floor_ms FROM sdai_sync_state WHERE id = 1), 0)
           )::text, 13, '0')
        || ':' || lpad(nextval('sdai_hlc_counter')::text, 5, '0')
        || ':' || COALESCE(NULLIF(current_setting('sdai.node_id', true), ''), 'local')
$$ LANGUAGE sql VOLATILE
""",
    # Generated columns (batch_document_pages.ocr_tsv) are computed by each
    # node from the columns that are synced - never sent: a peer can't
    # write them, and they can be large.
    r"""
CREATE OR REPLACE FUNCTION sdai_generated_cols(p_rel regclass) RETURNS text[] AS $$
    SELECT COALESCE(array_agg(attname::text), '{}')
    FROM pg_attribute
    WHERE attrelid = p_rel AND attnum > 0 AND NOT attisdropped AND attgenerated <> ''
$$ LANGUAGE sql STABLE
""",
    # TG_ARGV[0] is 'clock' (mutable table, keep sdai_sync_row_clock) or
    # 'noclock' (append-only), the rest are the primary-key columns.
    # The stamp is the row's own hlc when the sdai_touch_row trigger just
    # set it, so the row and its changelog entry carry the same value.
    r"""
CREATE OR REPLACE FUNCTION sdai_capture_change() RETURNS trigger AS $$
DECLARE
    v_new   JSONB;
    v_old   JSONB;
    v_pk    JSONB := '{}'::jsonb;
    v_data  JSONB;
    v_base  JSONB;
    v_clock JSONB;
    v_stamp TEXT;
    v_node  TEXT := COALESCE(NULLIF(current_setting('sdai.node_id', true), ''), 'local');
    i       INTEGER;
BEGIN
    IF current_setting('sdai.applying_sync', true) = 'on' THEN
        RETURN NULL;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        v_new := to_jsonb(NEW) - sdai_generated_cols(TG_RELID);
    END IF;
    IF TG_OP <> 'INSERT' THEN
        v_old := to_jsonb(OLD) - sdai_generated_cols(TG_RELID);
    END IF;
    FOR i IN 1 .. TG_NARGS - 1 LOOP
        v_pk := v_pk || jsonb_build_object(TG_ARGV[i], COALESCE(v_new, v_old) -> TG_ARGV[i]);
    END LOOP;

    IF TG_OP = 'INSERT' THEN
        v_data := v_new;
    ELSIF TG_OP = 'UPDATE' THEN
        SELECT jsonb_object_agg(n.key, n.value) INTO v_data
            FROM jsonb_each(v_new) n
            WHERE n.key NOT IN ('updated_at', 'origin_node', 'hlc')
              AND n.value IS DISTINCT FROM v_old -> n.key;
        IF v_data IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;
    v_stamp := COALESCE(CASE WHEN TG_OP <> 'DELETE' THEN v_new ->> 'hlc' END, sdai_hlc_now());

    IF TG_ARGV[0] = 'clock' THEN
        IF TG_OP = 'INSERT' THEN
            INSERT INTO sdai_sync_row_clock (table_name, pk, field_hlcs)
                VALUES (TG_TABLE_NAME, v_pk::text, jsonb_build_object('*', v_stamp))
                ON CONFLICT (table_name, pk)
                DO UPDATE SET field_hlcs = jsonb_build_object('*', v_stamp);
        ELSIF TG_OP = 'UPDATE' THEN
            SELECT field_hlcs INTO v_clock FROM sdai_sync_row_clock
                WHERE table_name = TG_TABLE_NAME AND pk = v_pk::text;
            SELECT jsonb_object_agg(k, COALESCE(v_clock -> k, v_clock -> '*')) INTO v_base
                FROM jsonb_object_keys(v_data) k
                WHERE COALESCE(v_clock -> k, v_clock -> '*') IS NOT NULL;
            INSERT INTO sdai_sync_row_clock (table_name, pk, field_hlcs)
                SELECT TG_TABLE_NAME, v_pk::text, jsonb_object_agg(k, v_stamp)
                FROM jsonb_object_keys(v_data) k
                ON CONFLICT (table_name, pk)
                DO UPDATE SET field_hlcs = sdai_sync_row_clock.field_hlcs || EXCLUDED.field_hlcs;
        ELSE
            DELETE FROM sdai_sync_row_clock WHERE table_name = TG_TABLE_NAME AND pk = v_pk::text;
        END IF;
    END IF;

    INSERT INTO sdai_sync_changelog (table_name, pk, op, row_data, base_hlcs, hlc, origin_node)
        VALUES (TG_TABLE_NAME, v_pk, lower(TG_OP), v_data, v_base, v_stamp, v_node);
    RETURN NULL;
END
$$ LANGUAGE plpgsql
""",
    # A dynamic table was created or gained columns: record its full
    # current column list so the peer can run _ensure_table before any row
    # of it arrives. Skipped while applying a peer's own ddl event.
    r"""
CREATE OR REPLACE FUNCTION sdai_log_ddl(p_table TEXT) RETURNS void AS $$
BEGIN
    IF current_setting('sdai.applying_sync', true) = 'on' THEN
        RETURN;
    END IF;
    INSERT INTO sdai_sync_changelog (table_name, pk, op, row_data, hlc, origin_node)
        SELECT p_table, '{}'::jsonb, 'ddl',
               jsonb_build_object('columns', jsonb_object_agg(column_name, data_type)),
               sdai_hlc_now(),
               COALESCE(NULLIF(current_setting('sdai.node_id', true), ''), 'local')
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = p_table AND is_generated = 'NEVER'
        HAVING count(*) > 0;
END
$$ LANGUAGE plpgsql
""",
)


def _trigger_sql(table: str, pk: tuple[str, ...], insert_only: bool) -> str:
    events = "INSERT" if insert_only else "INSERT OR UPDATE OR DELETE"
    args = ", ".join(f"'{a}'" for a in ("noclock" if insert_only else "clock", *pk))
    return (
        f'CREATE OR REPLACE TRIGGER sdai_capture_change AFTER {events} ON "{table}"'
        f" FOR EACH ROW EXECUTE FUNCTION sdai_capture_change({args})"
    )


async def install_dynamic_trigger(conn, table_name: str) -> None:
    """Capture trigger on one per-type table (called by _ensure_table)."""
    await conn.execute(text(_trigger_sql(table_name, ("id",), False)))


async def log_ddl(conn, table_name: str) -> None:
    await conn.execute(text("SELECT sdai_log_ddl(:t)"), {"t": table_name})


async def dynamic_tables(conn) -> list[str]:
    """Every per-type document table (t_<tenant>_<type>), oldest tenant
    first - identified like everywhere else in the codebase, by its
    source_image_path column."""
    rows = await conn.execute(
        text("""
            SELECT DISTINCT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'source_image_path'
              AND table_name NOT IN ('batches', 'batch_documents')
            ORDER BY table_name
        """)
    )
    return [r[0] for r in rows]


async def _seed_table(conn, table: SyncTable) -> None:
    """One insert change per row. A row that already has a row clock (a
    reseed, or changes captured earlier in the same startup) keeps it and
    sends it along in base_hlcs, so a peer that already holds the row
    merges field by field on the real stamps - a reseed never lets an old
    value overwrite a newer edit the peer has not sent yet."""
    pk_expr = ", ".join(f"'{c}', to_jsonb(r) -> '{c}'" for c in table.pk)
    order = f" ORDER BY {table.order_by}" if table.order_by else ""
    params = {"t": table.name, "node": await _node(conn)}
    row_json = f"(to_jsonb(r) - sdai_generated_cols('\"{table.name}\"'::regclass))"
    if table.insert_only:
        await conn.execute(
            text(
                "INSERT INTO sdai_sync_changelog (table_name, pk, op, row_data, hlc, origin_node)"
                f" SELECT :t, jsonb_build_object({pk_expr}), 'insert', {row_json}, sdai_hlc_now(), :node"
                f' FROM (SELECT * FROM "{table.name}"{order}) r'
            ),
            params,
        )
        return
    await conn.execute(
        text(f"""
            WITH src AS (
                SELECT jsonb_build_object({pk_expr}) AS pk, {row_json} AS row_data,
                       sdai_hlc_now() AS fresh, row_number() OVER () AS n
                FROM (SELECT * FROM "{table.name}"{order}) r
            ), ins AS (
                INSERT INTO sdai_sync_changelog (table_name, pk, op, row_data, base_hlcs, hlc, origin_node)
                SELECT :t, s.pk, 'insert', s.row_data,
                       jsonb_build_object('*', s.fresh) || COALESCE(c.field_hlcs, '{{}}'::jsonb),
                       s.fresh, :node
                FROM src s
                LEFT JOIN sdai_sync_row_clock c ON c.table_name = :t AND c.pk = s.pk::text
                ORDER BY s.n
                RETURNING table_name, pk, base_hlcs
            )
            INSERT INTO sdai_sync_row_clock (table_name, pk, field_hlcs)
            SELECT table_name, pk::text, base_hlcs FROM ins
            ON CONFLICT (table_name, pk) DO UPDATE SET field_hlcs = EXCLUDED.field_hlcs
        """),
        params,
    )


async def _node(conn) -> str:
    return (
        await conn.execute(
            text(
                "SELECT COALESCE(NULLIF(current_setting('sdai.node_id', true), ''), 'local')"
            )
        )
    ).scalar()


async def seed_changelog(conn, force: bool = False) -> bool:
    """Record every row as an insert, once per database (or again with
    force=True, e.g. after the changelog was pruned and a peer needs to
    start over), so a peer starting empty can get the whole deployment.
    Earlier changelog rows are dropped first (they are superseded by the
    rows' current state); row clocks are kept and travel with the seed.
    A new epoch tells peers holding a cursor into the old changelog to
    start from the beginning. Returns True when it seeded."""
    seeded = (
        await conn.execute(
            text("SELECT seeded_at FROM sdai_sync_state WHERE id = 1 FOR UPDATE")
        )
    ).scalar()
    if seeded is not None and not force:
        return False
    await conn.execute(text("TRUNCATE sdai_sync_changelog"))
    for table in SYNC_TABLES:
        await _seed_table(conn, table)
        if table.name == _DYNAMIC_AFTER:
            for name in await dynamic_tables(conn):
                await log_ddl(conn, name)
                await _seed_table(conn, SyncTable(name, order_by="ingested_at"))
    await conn.execute(
        text(
            "UPDATE sdai_sync_state SET seeded_at = now(), epoch = gen_random_uuid(),"
            " pruned_through = 0 WHERE id = 1"
        )
    )
    # No peer has any of the new changelog yet.
    await conn.execute(text("UPDATE sdai_sync_cursors SET sent_id = 0"))
    return True


async def install_capture(conn) -> None:
    """Startup: capture triggers on every static synced table and every
    existing per-type table (_backfill_sync_triggers), then seed the
    changelog if this database never was. Idempotent."""
    for table in SYNC_TABLES:
        await conn.execute(text(_trigger_sql(table.name, table.pk, table.insert_only)))
    for name in await dynamic_tables(conn):
        await install_dynamic_trigger(conn, name)
    await seed_changelog(conn)
