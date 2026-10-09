"""
Field history (GED Phase 2) - the before/after value of every changed
extraction field, in sdai_field_history.

Written by every path that changes extracted content, inside that change's
own transaction (a change without its history row, or the reverse, is
never committed):
  edit       documents_router.update_document_fields
  review     review_router.patch_review (approve with corrections)
  reextract  ingest_router._apply_reextraction (new file version re-run
             through the pipeline into the same document)
  sync       reserved for the local/cloud sync (Phase 8)

No router imports here, so ingest_router can use it at module level.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text

CHANGE_SOURCES = ("edit", "review", "reextract", "sync")


def json_value(v: Any) -> Any:
    """One column value as stored in a JSONB history cell. Lists/dicts stay
    structured, dates become ISO strings, everything else that isn't a
    JSON scalar becomes its string form. A JSONB column holding a
    serialized string (how _store_extraction writes list fields) is
    decoded back into the list, so the same value compares equal however
    it was written."""
    if v is None or isinstance(v, (bool, int, float, list, dict)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _comparable(v: Any) -> Any:
    v = json_value(v)
    if v == "":
        return None
    if isinstance(v, str) and v[:1] in ("[", "{"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def field_changes(
    old: Mapping[str, Any], new: Mapping[str, Any], fields: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """{field: {"old": ..., "new": ...}} for each of `fields` whose value
    differs between the old and new row. An empty string and NULL count as
    the same (the field editor clears a field by sending "")."""
    changes: dict[str, dict[str, Any]] = {}
    for field in fields:
        before, after = _comparable(old.get(field)), _comparable(new.get(field))
        if before != after:
            changes[field] = {"old": before, "new": after}
    return changes


async def record_field_changes(
    conn,
    *,
    tenant_id: str,
    table_name: str,
    document_id: str,
    changes: Mapping[str, Mapping[str, Any]],
    changed_by: str | None,
    source: str,
    version_no: int | None,
) -> None:
    """Insert one sdai_field_history row per changed field. `conn` is the
    caller's transaction - the history commits (or rolls back) with the
    change it describes."""
    if source not in CHANGE_SOURCES:
        raise ValueError(f"unknown change source {source!r}")
    for field, change in changes.items():
        await conn.execute(
            text("""
                INSERT INTO sdai_field_history
                    (tenant_id, table_name, document_id, field, old_value, new_value,
                     changed_by, change_source, version_no)
                VALUES (CAST(:tid AS uuid), :t, CAST(:doc AS uuid), :field,
                        CAST(:old AS jsonb), CAST(:new AS jsonb),
                        CAST(:by AS uuid), :source, :version)
            """),
            {
                "tid": tenant_id,
                "t": table_name,
                "doc": document_id,
                "field": field,
                "old": json.dumps(change.get("old"), ensure_ascii=False),
                "new": json.dumps(change.get("new"), ensure_ascii=False),
                "by": changed_by,
                "source": source,
                "version": version_no,
            },
        )
