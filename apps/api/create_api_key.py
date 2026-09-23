#!/usr/bin/env python3
"""
Mint, list, or revoke an external API key for a tenant (ministry) in the
sdai_api_keys table.

An API key lets an external/programmatic caller authenticate to
POST /api/ingest/upload (and poll GET /api/ingest/status/{batch_id}) without
a human login session, by sending `Authorization: Bearer <key>` instead of
the usual JWT cookie. It never carries admin or reviewer privilege - see
get_current_user's Authorization-header branch in auth.py.

Usage:
  # Mint a new key for a tenant - prints the full secret ONCE. Store it
  # somewhere safe (a secrets manager, a password vault) and hand it to the
  # external caller out of band (never over email/chat in plaintext); it
  # cannot be shown again, only revoked and replaced with a new one.
  python create_api_key.py --tenant land --name "partner-x-integration"

  # Same, but the key stops working automatically after 90 days.
  python create_api_key.py --tenant land --name "partner-x-integration" --expires-days 90

  # List a tenant's keys (never prints the secret itself, only its prefix).
  python create_api_key.py --tenant land --list

  # Revoke a key immediately (takes the key's id, from --list).
  python create_api_key.py --revoke <key-id>
"""

import argparse
import asyncio
import hashlib
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai")

# Matches auth.API_KEY_PREFIX - get_current_user relies on this exact prefix
# to recognize an Authorization header as an API key without a DB round trip.
KEY_PREFIX = "sdai_"


async def mint(tenant_slug: str, name: str, expires_days: int | None) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        tenant_row = await conn.execute(
            text("SELECT id FROM sdai_tenants WHERE slug = :slug"),
            {"slug": tenant_slug},
        )
        tenant = tenant_row.one_or_none()
        if tenant is None:
            print(
                f"Error: tenant '{tenant_slug}' not found - create it first with"
                f' create_tenant.py --slug {tenant_slug} --name "..."',
                file=sys.stderr,
            )
            await engine.dispose()
            sys.exit(1)

        secret = KEY_PREFIX + secrets.token_urlsafe(32)
        hashed = hashlib.sha256(secret.encode()).hexdigest()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None
        )

        result = await conn.execute(
            text("""
                INSERT INTO sdai_api_keys (tenant_id, name, key_prefix, hashed_key, expires_at)
                VALUES (:tenant_id, :name, :prefix, :hashed, :expires_at)
                RETURNING id
            """),
            {
                "tenant_id": tenant[0],
                "name": name,
                "prefix": secret[: len(KEY_PREFIX) + 8],
                "hashed": hashed,
                "expires_at": expires_at,
            },
        )
        key_id = result.scalar()

    print(f"API key created - id={key_id}  tenant={tenant_slug}  name={name!r}")
    if expires_at:
        print(f"Expires: {expires_at.isoformat()}")
    print()
    print("Secret (shown once - store it now, it cannot be retrieved again):")
    print()
    print(f"  {secret}")
    print()
    print("The external caller sends it as:  Authorization: Bearer <secret>")
    await engine.dispose()


async def list_keys(tenant_slug: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.connect() as conn:
        rows = await conn.execute(
            text("""
                SELECT k.id, k.name, k.key_prefix, k.is_active, k.expires_at, k.created_at
                FROM sdai_api_keys k
                JOIN sdai_tenants t ON t.id = k.tenant_id
                WHERE t.slug = :slug
                ORDER BY k.created_at DESC
            """),
            {"slug": tenant_slug},
        )
        keys = rows.all()

    if not keys:
        print(f"No API keys for tenant '{tenant_slug}'.")
    for k in keys:
        status = "active" if k[3] else "revoked"
        expires = f"  expires={k[4].isoformat()}" if k[4] else ""
        print(f"{k[0]}  {status:8s} {k[2]}...  {k[1]!r}  created={k[5].isoformat()}{expires}")
    await engine.dispose()


async def revoke(key_id: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE sdai_api_keys SET is_active = false WHERE id = CAST(:id AS uuid)"),
            {"id": key_id},
        )
        if result.rowcount == 0:
            print(f"Error: no API key with id '{key_id}'", file=sys.stderr)
            await engine.dispose()
            sys.exit(1)

    print(f"API key {key_id} revoked.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mint, list, or revoke an SDAI external API key")
    parser.add_argument("--tenant", help="Tenant slug (required to mint or list)")
    parser.add_argument("--name", help="Label for the key, e.g. 'partner-x-integration'")
    parser.add_argument(
        "--expires-days", type=int, default=None, help="Optional: key stops working after N days"
    )
    parser.add_argument("--list", action="store_true", help="List a tenant's keys instead of minting")
    parser.add_argument("--revoke", metavar="KEY_ID", help="Revoke a key by id instead of minting")
    args = parser.parse_args()

    if args.revoke:
        asyncio.run(revoke(args.revoke))
    elif args.list:
        if not args.tenant:
            print("Error: --list requires --tenant", file=sys.stderr)
            sys.exit(1)
        asyncio.run(list_keys(args.tenant))
    else:
        if not args.tenant or not args.name:
            print("Error: minting a key requires --tenant and --name", file=sys.stderr)
            sys.exit(1)
        asyncio.run(mint(args.tenant, args.name, args.expires_days))
