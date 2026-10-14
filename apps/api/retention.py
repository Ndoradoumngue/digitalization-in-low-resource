"""Retention rules and the daily retention job (GED Phase 6.3).

A rule (sdai_retention_rules) gives a duration in months, the date it runs
from (trigger_on) and what happens when it ends (final_action):

  scope        series (the series and everything below it) | doc_type (a per-type table)
  trigger_on   document_date (the extracted field `date_field`) | ingested_at |
               reviewed_at | archived_at
  final_action destroy | review (both make an archived document
               destruction_eligible - destruction itself always needs a
               proposal and an approval, "review" only tells the archivist
               the outcome is to be re-examined, e.g. extended) |
               keep_forever (no retention date - never eligible)

The most specific rule wins: a series rule, the nearest series up the tree
first, before the document type's rule (pick_rule). The result is stored on
the document - retention_until + retention_rule_id - and recomputed
(recompute) when a rule changes, a document moves to another series or a
series moves, a date it depends on changes (field edit, review,
re-extraction, archiving) and by the daily job, which also catches changes
that arrived through sync. A manual date set by an archivist
(retention_override, lifecycle.extend) wins over the rules.

The daily job (run_once, local node only) marks archived documents whose
retention_until has passed destruction_eligible (lifecycle.mark_eligible -
held documents are skipped) and notifies the tenant's lifecycle managers,
then purges the trash (destruction.purge_trash).
"""

from __future__ import annotations

import asyncio
import calendar
import os
import re
import sys
from datetime import date, datetime, timezone

import lifecycle
from sqlalchemy import text

TRIGGERS = ("document_date", "ingested_at", "reviewed_at", "archived_at")
FINAL_ACTIONS = ("destroy", "keep_forever", "review")
RETENTION_JOB_INTERVAL_HOURS = float(os.getenv("RETENTION_JOB_INTERVAL_HOURS", "24"))

_FIELD_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TABLE_RE = re.compile(r"^[a-z0-9_]+$")

# Date formats tried on an extracted date field (after ISO).
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y")
_FR_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}


def add_months(d: date, months: int) -> date:
    """`d` plus `months` calendar months, clamped to the month's last day."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def parse_date(value) -> date | None:
    """A date from a stored value: a date/datetime, an ISO string, a
    day-first numeric date (08/03/2021) or a French long date (8 mars 2021).
    None when nothing sensible is found."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})$", s.lower())
    if m and m.group(2) in _FR_MONTHS:
        try:
            return date(int(m.group(3)), _FR_MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    return None


def pick_rule(rules: dict, series_path: str | None, table: str) -> dict | None:
    """The rule governing a document: the rule of its series or of the
    nearest series above it, else its document type's rule."""
    if series_path:
        for series_id in reversed([p for p in series_path.split("/") if p]):
            rule = rules["series"].get(series_id)
            if rule is not None:
                return rule
    return rules["doc_type"].get(table)


def compute_until(rule: dict | None, row: dict) -> date | None:
    """retention_until for a document under `rule` (None: no rule, keep
    forever, or the date it runs from is not known yet - e.g. archived_at
    on a document not archived)."""
    if rule is None or rule["final_action"] == "keep_forever":
        return None
    trigger = rule["trigger_on"]
    if trigger == "document_date":
        start = (
            parse_date(row.get(f"__date_{rule['date_field']}"))
            if rule["date_field"]
            else None
        )
    else:
        start = parse_date(row.get(trigger))
    if start is None:
        return None
    return add_months(start, int(rule["duration_months"]))


async def load_rules(conn, tenant_id: str) -> dict:
    rows = (
        (
            await conn.execute(
                text("""
                SELECT id::text AS id, name, scope, series_id::text AS series_id, table_name,
                       duration_months, trigger_on, date_field, final_action
                FROM sdai_retention_rules WHERE tenant_id = CAST(:tid AS uuid)
            """),
                {"tid": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    rules: dict = {"series": {}, "doc_type": {}}
    for r in rows:
        r = dict(r)
        if r["scope"] == "series":
            rules["series"][r["series_id"]] = r
        else:
            rules["doc_type"][r["table_name"]] = r
    return rules


async def _tenant_tables(conn, tenant_id: str) -> list[str]:
    slug = (
        await conn.execute(
            text("SELECT slug FROM sdai_tenants WHERE id = CAST(:tid AS uuid)"),
            {"tid": tenant_id},
        )
    ).scalar()
    if slug is None:
        return []
    rows = await conn.execute(
        text("""
            SELECT DISTINCT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'source_image_path'
              AND starts_with(table_name, :prefix)
        """),
        {"prefix": f"t_{slug}_"},
    )
    return sorted(r[0] for r in rows)


async def recompute(
    conn,
    tenant_id: str,
    *,
    tables: list[str] | None = None,
    doc_ids: list[str] | None = None,
) -> int:
    """Recompute retention_until / retention_rule_id of the tenant's
    documents (all, or `doc_ids` of `tables`) from the rules. Destroyed
    rows are left alone; a manual retention_override wins. Returns the
    number of rows changed."""
    rules = await load_rules(conn, tenant_id)
    all_tables = await _tenant_tables(conn, tenant_id)
    tables = [t for t in (tables or all_tables) if t in all_tables]
    date_fields = {
        r["date_field"]
        for r in [*rules["series"].values(), *rules["doc_type"].values()]
        if r["trigger_on"] == "document_date"
        and r["date_field"]
        and _FIELD_RE.match(r["date_field"])
    }
    changed = 0
    for table in tables:
        assert _TABLE_RE.fullmatch(table), table
        cols = set(
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = 'public' AND table_name = :t"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )
        extra = "".join(
            f', d."{f}"::text AS "__date_{f}"' for f in sorted(date_fields) if f in cols
        )
        where = "COALESCE(d.lifecycle_status, 'active') <> 'destroyed'"
        params: dict = {}
        if doc_ids is not None:
            where += " AND d.id = ANY(CAST(:ids AS uuid[]))"
            params["ids"] = list(doc_ids)
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT d.id::text AS id, s.path AS series_path, d.ingested_at,
                           {"d.reviewed_at" if "reviewed_at" in cols else "NULL::timestamptz"}
                               AS reviewed_at,
                           d.archived_at, d.retention_until, d.retention_rule_id::text AS rule_id,
                           d.retention_override{extra}
                    FROM "{table}" d LEFT JOIN sdai_series s ON s.id = d.series_id
                    WHERE {where}
                """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            rule = pick_rule(rules, row["series_path"], table)
            until = row["retention_override"] or compute_until(rule, row)
            rule_id = rule["id"] if rule else None
            if until == row["retention_until"] and rule_id == row["rule_id"]:
                continue
            await conn.execute(
                text(f"""
                    UPDATE "{table}" SET retention_until = :u,
                           retention_rule_id = CAST(:r AS uuid)
                    WHERE id = CAST(:id AS uuid)
                """),
                {"u": until, "r": rule_id, "id": row["id"]},
            )
            changed += 1
    return changed


async def recompute_quietly(conn, tenant_id: str, table: str, doc_id: str) -> None:
    """recompute for one document from a path whose main change must not
    fail because of it (ingestion, re-extraction) - in a savepoint; the
    daily job catches up anything missed."""
    try:
        async with conn.begin_nested():
            await recompute(conn, tenant_id, tables=[table], doc_ids=[doc_id])
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"[retention] WARNING: recompute failed for {table}/{doc_id}: {exc}",
            file=sys.stderr,
        )


# ── Daily job ─────────────────────────────────────────────────────────────────


async def lifecycle_managers(conn, tenant_id: str) -> list[str]:
    """Active users of the tenant who manage the lifecycle (admins and
    reviewers delegated can_manage_lifecycle)."""
    rows = await conn.execute(
        text("""
            SELECT id::text FROM sdai_users
            WHERE tenant_id = CAST(:tid AS uuid) AND is_active IS NOT FALSE
              AND (role = 'admin' OR (role = 'reviewer' AND can_manage_lifecycle))
        """),
        {"tid": tenant_id},
    )
    return [r[0] for r in rows]


async def notify(
    conn, tenant_id: str, user_ids: list[str], kind: str, payload: dict
) -> None:
    import json

    for user_id in user_ids:
        await conn.execute(
            text("""
                INSERT INTO sdai_notifications (tenant_id, user_id, kind, payload)
                VALUES (CAST(:tid AS uuid), CAST(:u AS uuid), :k, CAST(:p AS jsonb))
            """),
            {
                "tid": tenant_id,
                "u": user_id,
                "k": kind,
                "p": json.dumps(payload, default=str),
            },
        )


async def mark_eligible_documents(conn, tenant_id: str, today: date) -> list[dict]:
    """archived → destruction_eligible for every archived, untrashed
    document of the tenant whose retention date has passed and whose rule
    (or manual date) calls for an outcome. Held documents are skipped by
    lifecycle.mark_eligible."""
    eligible = []
    for table in await _tenant_tables(conn, tenant_id):
        rows = (
            (
                await conn.execute(
                    text(f"""
                    SELECT d.id::text AS id, d.record_id, d.retention_until,
                           r.final_action, r.name AS rule_name
                    FROM "{table}" d
                    LEFT JOIN sdai_retention_rules r ON r.id = d.retention_rule_id
                    WHERE d.lifecycle_status = 'archived' AND d.deleted_at IS NULL
                      AND d.retention_until IS NOT NULL AND d.retention_until <= :today
                      AND (r.final_action IS NULL OR r.final_action <> 'keep_forever')
                """),
                    {"today": today},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            if await lifecycle.mark_eligible(conn, table, row["id"], today):
                eligible.append({"table_name": table, **dict(row)})
    return eligible


async def run_once(engine, today: date | None = None) -> dict:
    """One pass of the daily job over every tenant: recompute retention
    dates, mark expired archived documents destruction_eligible (audited,
    archivists notified), purge the trash. Returns counts."""
    from audit import log_action
    import destruction

    today = today or datetime.now(timezone.utc).date()
    totals = {"recomputed": 0, "eligible": 0, "purged": 0}
    async with engine.connect() as conn:
        tenants = (
            (await conn.execute(text("SELECT id::text FROM sdai_tenants")))
            .scalars()
            .all()
        )
    for tenant_id in tenants:
        async with engine.begin() as conn:
            totals["recomputed"] += await recompute(conn, tenant_id)
            eligible = await mark_eligible_documents(conn, tenant_id, today)
            if eligible:
                await notify(
                    conn,
                    tenant_id,
                    await lifecycle_managers(conn, tenant_id),
                    "retention_eligible",
                    {
                        "count": len(eligible),
                        "documents": [
                            {
                                "table_name": d["table_name"],
                                "id": d["id"],
                                "record_id": d["record_id"],
                                "final_action": d["final_action"] or "review",
                            }
                            for d in eligible[:50]
                        ],
                    },
                )
        for d in eligible:
            await log_action(
                action="document_destruction_eligible",
                table_name=d["table_name"],
                document_id=d["id"],
                details={
                    "record_id": d["record_id"],
                    "retention_until": d["retention_until"].isoformat(),
                    "rule": d["rule_name"],
                    "final_action": d["final_action"] or "review",
                },
            )
        totals["eligible"] += len(eligible)
        totals["purged"] += await destruction.purge_trash(engine, tenant_id)
    return totals


async def daily_worker() -> None:
    """Background task on the local node: run_once shortly after startup,
    then every RETENTION_JOB_INTERVAL_HOURS. A session advisory lock keeps
    two API processes from running it at the same time."""
    from ingest_router import _engine

    await asyncio.sleep(30)
    while True:
        try:
            engine = _engine()
            async with engine.connect() as lock_conn:
                got = (
                    await lock_conn.execute(
                        text(
                            "SELECT pg_try_advisory_lock(hashtext('sdai_retention_job'))"
                        )
                    )
                ).scalar()
                if got:
                    try:
                        totals = await run_once(engine)
                        print(f"[retention] {totals}", file=sys.stderr)
                    finally:
                        await lock_conn.execute(
                            text(
                                "SELECT pg_advisory_unlock(hashtext('sdai_retention_job'))"
                            )
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[retention] WARNING: daily job failed: {exc}", file=sys.stderr)
        await asyncio.sleep(RETENTION_JOB_INTERVAL_HOURS * 3600)
