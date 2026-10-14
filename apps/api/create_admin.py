#!/usr/bin/env python3
"""
Seed a user into the sdai_users table, in a given tenant.

Usage:
  python create_admin.py --email admin@example.com --full-name "Alice" --tenant default
  python create_admin.py --email reviewer@example.com --tenant land --role reviewer
  python create_admin.py --email someone@example.com --deactivate

The password is asked for (or read from SDAI_ADMIN_PASSWORD); --password
also works but shows it to every user of the server (ps).

The tenant must already exist - create it first with create_tenant.py.
Upserts by email: running the script again updates the password, role,
tenant, and re-activates the account. --deactivate blocks an account
(sign-in refused, open sessions rejected on their next request) without
deleting it - its name stays on everything it did.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys

import bcrypt as _bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://sdai:sdai@localhost:5432/sdai")


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
                f"Error: tenant '{tenant_slug}' not found - create it first with"
                f' create_tenant.py --slug {tenant_slug} --name "..."',
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
                "email": email,
                "hashed": hashed,
                "full_name": full_name,
                "role": role,
                "tenant_id": tenant[0],
            },
        )
        row = result.one()

    print(f"User ready - id={row[0]}  email={row[1]}  role={row[2]}  tenant={tenant_slug}")
    await engine.dispose()


async def deactivate(email: str) -> int:
    engine = create_async_engine(DATABASE_URL, echo=False)
    try:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "UPDATE sdai_users SET is_active = false WHERE email = :e"
                        " RETURNING id, email"
                    ),
                    {"e": email},
                )
            ).one_or_none()
            if row is None:
                print(f"Error: no user '{email}'", file=sys.stderr)
                return 1
            await conn.execute(
                text(
                    "INSERT INTO sdai_audit_log (user_id, user_email, action, details)"
                    " VALUES (:uid, :email, 'user_deactivated', CAST(:d AS jsonb))"
                ),
                {"uid": row[0], "email": row[1], "d": json.dumps({"via": "create_admin.py"})},
            )
    finally:
        await engine.dispose()
    print(f"User deactivated - {email} (run this script without --deactivate to re-activate)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update an SDAI user")
    parser.add_argument("--email", required=True, help="Login email address")
    parser.add_argument(
        "--password",
        default=None,
        help="Initial password. Omit it to read SDAI_ADMIN_PASSWORD or be prompted -"
        " a password on the command line is visible to every user of the server (ps).",
    )
    parser.add_argument("--full-name", default="", help="Display name (optional)")
    parser.add_argument(
        "--tenant", default=None, help="Tenant slug the user belongs to (must already exist)"
    )
    parser.add_argument(
        "--deactivate", action="store_true", help="Block this account instead (keeps its history)"
    )
    parser.add_argument(
        "--role",
        default="admin",
        choices=["admin", "reviewer", "reader"],
        help="Role within the tenant (default: admin, preserving this script's original behavior)",
    )
    args = parser.parse_args()

    if args.deactivate:
        sys.exit(asyncio.run(deactivate(args.email)))
    if not args.tenant:
        parser.error("--tenant is required")
    if args.password is None:
        args.password = os.environ.get("SDAI_ADMIN_PASSWORD") or (
            getpass.getpass("Password: ") if sys.stdin.isatty() else ""
        )
    if len(args.password) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.email, args.password, args.full_name, args.tenant, args.role))
