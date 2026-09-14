"""
Shared JWT auth dependency — PostgreSQL-backed user lookup with role support.

Provides:
  get_current_user  — FastAPI dependency; returns CurrentUser or raises 401
  require_admin     — FastAPI dependency; returns CurrentUser or raises 403
"""

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from jose import JWTError, jwt

SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("AUTH_SECRET_KEY environment variable is not set")
ALGORITHM = "HS256"


@dataclass
class CurrentUser:
    id:        str
    email:     str
    full_name: Optional[str]
    role:      str


async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
) -> CurrentUser:
    """FastAPI dependency — raises 401 if the JWT cookie is missing, invalid, or revoked."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        token   = access_token.removeprefix("Bearer ")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: Optional[str] = payload.get("sub")
        jti:     Optional[str] = payload.get("jti")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Lazy import avoids circular dependency (ingest_router imports auth at module level).
    from ingest_router import _engine  # noqa: PLC0415
    from sqlalchemy import text

    async with _engine().connect() as conn:
        # Reject blocklisted tokens (e.g. after explicit logout)
        if jti:
            bl = await conn.execute(
                text("SELECT 1 FROM sdai_token_blocklist WHERE jti = :jti"),
                {"jti": jti},
            )
            if bl.one_or_none() is not None:
                raise HTTPException(status_code=401, detail="Token revoked")

        row = await conn.execute(
            text(
                "SELECT id, email, full_name, role, is_active"
                " FROM sdai_users WHERE id = CAST(:id AS uuid)"
            ),
            {"id": user_id},
        )
        user = row.one_or_none()

    if user is None or not user[4]:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    return CurrentUser(
        id=str(user[0]),
        email=user[1],
        full_name=user[2],
        role=user[3],
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency — raises 403 unless the authenticated user has role='admin'."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
