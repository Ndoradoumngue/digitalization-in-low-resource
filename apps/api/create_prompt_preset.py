#!/usr/bin/env python3
"""
Create, update, list, or deactivate a named prompt preset for a tenant, in
the sdai_prompt_presets table.

A preset is an additional, admin-defined extraction config a tenant can
offer besides its own default (set on sdai_tenants via create_tenant.py).
An external API caller selects one by --key at upload time (the 'preset'
field on POST /api/ingest/upload) instead of the tenant's default - it can
never supply prompt text of its own, only pick from what ops has already
registered here. Same convention as create_tenant.py: the prompt file
itself is placed by ops directly on the documents/ volume mount, no rebuild
needed.

Usage:
  python create_prompt_preset.py --tenant land --key short-form \
      --label "Short-form land record" \
      --prompt-file /app/documents/prompts/land_short_form.txt

  python create_prompt_preset.py --tenant land --list

  python create_prompt_preset.py --tenant land --key short-form --deactivate

Upserts by (tenant, key): running again with the same --tenant/--key
updates that preset's config and re-activates it.
"""

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai")

_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


async def _tenant_id(conn, tenant_slug: str) -> str:
    row = await conn.execute(text("SELECT id FROM sdai_tenants WHERE slug = :slug"), {"slug": tenant_slug})
    tenant = row.one_or_none()
    if tenant is None:
        print(
            f"Error: tenant '{tenant_slug}' not found - create it first with"
            f' create_tenant.py --slug {tenant_slug} --name "..."',
            file=sys.stderr,
        )
        sys.exit(1)
    return tenant[0]


async def upsert(
    tenant_slug: str,
    key: str,
    label: str,
    prompt_file: str,
    list_fields: str | None,
    page_timeout_seconds: float | None,
    split_page_columns: bool | None,
) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        tenant_id = await _tenant_id(conn, tenant_slug)
        result = await conn.execute(
            text("""
                INSERT INTO sdai_prompt_presets
                    (tenant_id, key, label, prompt_file, list_fields,
                     page_timeout_seconds, split_page_columns)
                VALUES
                    (:tenant_id, :key, :label, :prompt_file, :list_fields,
                     :page_timeout_seconds, :split_page_columns)
                ON CONFLICT (tenant_id, key) DO UPDATE
                    SET label                = :label,
                        prompt_file          = :prompt_file,
                        list_fields          = :list_fields,
                        page_timeout_seconds = :page_timeout_seconds,
                        split_page_columns   = :split_page_columns,
                        is_active            = true
                RETURNING id
            """),
            {
                "tenant_id": tenant_id,
                "key": key,
                "label": label,
                "prompt_file": prompt_file,
                "list_fields": list_fields,
                "page_timeout_seconds": page_timeout_seconds,
                "split_page_columns": split_page_columns,
            },
        )
        preset_id = result.scalar()

    print(f"Prompt preset ready - id={preset_id}  tenant={tenant_slug}  key={key}  label={label!r}")
    await engine.dispose()


async def list_presets(tenant_slug: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT p.key, p.label, p.is_active, p.prompt_file
                FROM sdai_prompt_presets p
                JOIN sdai_tenants t ON t.id = p.tenant_id
                WHERE t.slug = :slug
                ORDER BY p.label
            """),
            {"slug": tenant_slug},
        )
        presets = rows.all()

    if not presets:
        print(f"No prompt presets for tenant '{tenant_slug}'.")
    for p in presets:
        status = "active" if p[2] else "inactive"
        print(f"{p[0]:20s} {status:9s} {p[1]!r}  ({p[3]})")
    await engine.dispose()


async def deactivate(tenant_slug: str, key: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        tenant_id = await _tenant_id(conn, tenant_slug)
        result = await conn.execute(
            text(
                "UPDATE sdai_prompt_presets SET is_active = false"
                " WHERE tenant_id = CAST(:tenant_id AS uuid) AND key = :key"
            ),
            {"tenant_id": tenant_id, "key": key},
        )
        if result.rowcount == 0:
            print(f"Error: no preset '{key}' for tenant '{tenant_slug}'", file=sys.stderr)
            await engine.dispose()
            sys.exit(1)

    print(f"Preset '{key}' deactivated for tenant '{tenant_slug}'.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create, update, list, or deactivate a prompt preset")
    parser.add_argument("--tenant", required=True, help="Tenant slug this preset belongs to")
    parser.add_argument("--key", help="Short lowercase identifier callers pass as 'preset', e.g. 'short-form'")
    parser.add_argument("--label", help="Human-readable label, e.g. 'Short-form land record'")
    parser.add_argument(
        "--prompt-file",
        help="Absolute path (inside the container) to this preset's extraction prompt,"
        " e.g. /app/documents/prompts/land_short_form.txt",
    )
    parser.add_argument(
        "--list-fields",
        default=None,
        help="Comma-separated field names unioned across pages for this preset."
        " Omit to inherit the tenant's own default.",
    )
    parser.add_argument("--page-timeout-seconds", type=float, default=None)
    parser.add_argument("--split-page-columns", action="store_true", default=None)
    parser.add_argument("--list", action="store_true", help="List this tenant's presets instead of upserting")
    parser.add_argument("--deactivate", action="store_true", help="Deactivate --key instead of upserting")
    args = parser.parse_args()

    if args.list:
        asyncio.run(list_presets(args.tenant))
    elif args.deactivate:
        if not args.key:
            print("Error: --deactivate requires --key", file=sys.stderr)
            sys.exit(1)
        asyncio.run(deactivate(args.tenant, args.key))
    else:
        if not args.key or not args.label or not args.prompt_file:
            print("Error: creating a preset requires --key, --label, and --prompt-file", file=sys.stderr)
            sys.exit(1)
        if not _KEY_RE.match(args.key):
            print(
                "Error: --key must start with a letter and contain only lowercase letters,"
                " digits, underscores, and hyphens (max 40 chars)",
                file=sys.stderr,
            )
            sys.exit(1)
        asyncio.run(
            upsert(
                args.tenant,
                args.key,
                args.label,
                args.prompt_file,
                args.list_fields,
                args.page_timeout_seconds,
                args.split_page_columns,
            )
        )
