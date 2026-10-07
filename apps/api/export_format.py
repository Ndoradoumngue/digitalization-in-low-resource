"""Value serializers shared by the full-archive export (admin_router) and
the per-document export (documents_router)."""

import csv
import io
import json


def serial(v):
    """One column value as a JSON-safe value."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "hex"):  # UUID object from asyncpg
        return str(v)
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


def sql_literal(v) -> str:
    """Render one Python value (as returned by asyncpg for a row column)
    as a SQL literal, for the .sql export format. Not parameterized SQL -
    this produces a standalone text file meant to be read or replayed
    later, not executed by this process, so the values must be inlined
    safely rather than bound."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict, list)):
        text_val = json.dumps(v, ensure_ascii=False).replace("'", "''")
        return f"'{text_val}'::jsonb"
    if hasattr(v, "isoformat"):
        return f"'{v.isoformat()}'"
    text_val = str(v).replace("'", "''")  # covers str, UUID, etc.
    return f"'{text_val}'"


def insert_statement(table_name: str, row: dict) -> str:
    cols = list(row.keys())
    col_clause = ", ".join(f'"{c}"' for c in cols)
    val_clause = ", ".join(sql_literal(row[c]) for c in cols)
    return f'INSERT INTO "{table_name}" ({col_clause}) VALUES ({val_clause});'


def list_to_csv(items: list) -> str:
    """A list field as CSV: one row per item, one column per key found in
    any item (in first-seen order). Scalar items get a single "value"
    column. Starts with a UTF-8 BOM so Excel reads accented text correctly."""
    columns: list[str] = []
    for item in items:
        keys = item.keys() if isinstance(item, dict) else ["value"]
        for k in keys:
            if k not in columns:
                columns.append(k)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for item in items:
        record = item if isinstance(item, dict) else {"value": item}
        writer.writerow(
            [
                json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else v)
                for v in (record.get(c) for c in columns)
            ]
        )
    return "﻿" + out.getvalue()
