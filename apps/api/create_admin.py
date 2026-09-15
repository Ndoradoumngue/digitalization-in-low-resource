#!/usr/bin/env python3
"""
Seed a user into the sdai_users table, in a given tenant.

Usage:
  python create_admin.py --email admin@example.com --password secret --full-name "Alice" --tenant default
  python create_admin.py --email reviewer@example.com --password secret --tenant land --role reviewer

The tenant must already exist — create it first with create_tenant.py.
Upserts by email: running the script again updates the password, role,
tenant, and re-activates the account.
"""

import argparse
import asyncio
import os
import sys

import bcrypt as _bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai"
)


async def main(email: str, password: str, full_name: str, tenant_slug: str, role: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

    async with engine.begin() as conn:
        tenant_row = await conn.execute(
            text("SELECT id FROM sdai_tenants WHERE slug = :slug"),
            {"slug": tenant_slug},
        )
        tenant = tenant_row.one_or_none()
        if tenant is None:
            print(
                f"Error: tenant '{tenant_slug}' not found — create it first with"
                f" create_tenant.py --slug {tenant_slug} --name \"...\"",
                file=sys.stderr,
            )
            await engine.dispose()
            sys.exit(1)

        result = await conn.execute(
            text("""
                INSERT INTO sdai_users (email, hashed_password, full_name, role, tenant_id)
                VALUES (:email, :hashed, :full_name, :role, :tenant_id)
                ON CONFLICT (email) DO UPDATE
                    SET hashed_password = :hashed,
                        full_name       = :full_name,
                        role            = :role,
                        tenant_id       = :tenant_id,
                        is_active       = true
                RETURNING id, email, role
            """),
            {
                "email": email, "hashed": hashed, "full_name": full_name,
                "role": role, "tenant_id": tenant[0],
            },
        )
        row = result.one()

    print(f"User ready — id={row[0]}  email={row[1]}  role={row[2]}  tenant={tenant_slug}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update an SDAI user")
    parser.add_argument("--email",     required=True,  help="Login email address")
    parser.add_argument("--password",  required=True,  help="Initial password")
    parser.add_argument("--full-name", default="",     help="Display name (optional)")
    parser.add_argument("--tenant",    required=True,  help="Tenant slug the user belongs to (must already exist)")
    parser.add_argument(
        "--role", default="admin", choices=["admin", "reviewer"],
        help="Role within the tenant (default: admin, preserving this script's original behavior)",
    )
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.email, args.password, args.full_name, args.tenant, args.role))
