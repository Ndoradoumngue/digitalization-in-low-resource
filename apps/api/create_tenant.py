#!/usr/bin/env python3
"""
Create or update a tenant (ministry/administration) in the sdai_tenants table.

Usage:
  python create_tenant.py --slug land --name "Ministry of Land" \
      --prompt-file /app/documents/prompts/land.txt

  python create_tenant.py --slug oil --name "Ministry of Oil" \
      --list-fields entries,quality_issues --page-timeout-seconds 180 --split-page-columns

Upserts by slug: running again updates the tenant's name/config and
re-activates it. The prompt file itself is placed by ops directly on the
documents/ volume mount (per README) — no rebuild needed to onboard a
ministry's schema.

Run 'create_admin.py --tenant <slug>' afterward to provision the tenant's
first user.
"""

import argparse
import asyncio
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai"
)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


async def main(
    slug: str,
    name: str,
    prompt_file: str | None,
    list_fields: str | None,
    page_timeout_seconds: float | None,
    split_page_columns: bool | None,
) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO sdai_tenants
                    (slug, name, prompt_file, list_fields, page_timeout_seconds, split_page_columns)
                VALUES
                    (:slug, :name, :prompt_file, :list_fields, :page_timeout_seconds, :split_page_columns)
                ON CONFLICT (slug) DO UPDATE
                    SET name                 = :name,
                        prompt_file          = :prompt_file,
                        list_fields          = :list_fields,
                        page_timeout_seconds = :page_timeout_seconds,
                        split_page_columns   = :split_page_columns,
                        is_active            = true
                RETURNING id, slug, name
            """),
            {
                "slug": slug, "name": name, "prompt_file": prompt_file,
                "list_fields": list_fields, "page_timeout_seconds": page_timeout_seconds,
                "split_page_columns": split_page_columns,
            },
        )
        row = result.one()

    print(f"Tenant ready — id={row[0]}  slug={row[1]}  name={row[2]}")
    print(f"Next: python create_admin.py --email <email> --password <pw> --tenant {row[1]}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update an SDAI tenant (ministry)")
    parser.add_argument("--slug", required=True, help="Short lowercase identifier, e.g. 'land' (used in table names)")
    parser.add_argument("--name", required=True, help="Display name, e.g. 'Ministry of Land'")
    parser.add_argument(
        "--prompt-file", default=None,
        help="Path to this tenant's VLM prompt file (e.g. /app/documents/prompts/land.txt)."
             " Omit to fall back to the deployment-wide VLM_PROMPT_FILE / built-in default.",
    )
    parser.add_argument(
        "--list-fields", default=None,
        help="Comma-separated field names unioned across pages (same semantics as VLM_LIST_FIELDS)."
             " Omit to fall back to the deployment-wide default.",
    )
    parser.add_argument(
        "--page-timeout-seconds", type=float, default=None,
        help="Per-page VLM call timeout override. Omit to fall back to the deployment-wide default.",
    )
    parser.add_argument(
        "--split-page-columns", action="store_true", default=None,
        help="Enable two-column page splitting for this tenant. Omit to fall back to the deployment-wide default.",
    )
    args = parser.parse_args()

    if not _SLUG_RE.match(args.slug):
        print(
            "Error: --slug must start with a letter and contain only lowercase"
            " letters, digits, and underscores (max 24 chars)",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.slug == "default":
        print(
            "Error: 'default' is a reserved tenant slug (auto-seeded for standalone"
            " deployments) — choose a different slug.",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(main(
        args.slug, args.name, args.prompt_file, args.list_fields,
        args.page_timeout_seconds, args.split_page_columns,
    ))
