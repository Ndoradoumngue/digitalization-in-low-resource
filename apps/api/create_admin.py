#!/usr/bin/env python3
"""
Seed an admin user into the sdai_users table.

Usage:
  python create_admin.py --email admin@example.com --password secret --full-name "Alice"

Upserts by email: running the script again updates the password and re-activates the account.
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


async def main(email: str, password: str, full_name: str) -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO sdai_users (email, hashed_password, full_name, role)
                VALUES (:email, :hashed, :full_name, 'admin')
                ON CONFLICT (email) DO UPDATE
                    SET hashed_password = :hashed,
                        full_name       = :full_name,
                        role            = 'admin',
                        is_active       = true
                RETURNING id, email, role
            """),
            {"email": email, "hashed": hashed, "full_name": full_name},
        )
        row = result.one()

    print(f"Admin user ready — id={row[0]}  email={row[1]}  role={row[2]}")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update an SDAI admin user")
    parser.add_argument("--email",     required=True,  help="Login email address")
    parser.add_argument("--password",  required=True,  help="Initial password")
    parser.add_argument("--full-name", default="",     help="Display name (optional)")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.email, args.password, args.full_name))
