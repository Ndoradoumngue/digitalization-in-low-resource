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
    id:                str
    email:             str
    full_name:         Optional[str]
    role:              str
    tenant_id:         str
    tenant_slug:       str
    tenant_name:       str
    can_manage_access: bool
    group_ids:         list[str]

    @property
    def can_manage_document_access(self) -> bool:
        """Admins can always tag documents with access grants; a reviewer
        needs the delegated can_manage_access flag."""
        return self.role == "admin" or self.can_manage_access


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
                "SELECT u.id, u.email, u.full_name, u.role, u.is_active,"
                "       t.id AS tenant_id, t.slug AS tenant_slug, t.name AS tenant_name,"
                "       t.is_active AS tenant_is_active, u.can_manage_access"
                " FROM sdai_users u"
                " JOIN sdai_tenants t ON t.id = u.tenant_id"
                " WHERE u.id = CAST(:id AS uuid)"
            ),
            {"id": user_id},
        )
        user = row.one_or_none()

        if user is None or not user[4] or not user[8]:
            raise HTTPException(status_code=401, detail="User not found or deactivated")

        group_rows = await conn.execute(
            text("SELECT group_id FROM sdai_user_groups WHERE user_id = CAST(:id AS uuid)"),
            {"id": user_id},
        )
        group_ids = [str(r[0]) for r in group_rows]

    return CurrentUser(
        id=str(user[0]),
        email=user[1],
        full_name=user[2],
        role=user[3],
        tenant_id=str(user[5]),
        tenant_slug=user[6],
        tenant_name=user[7],
        can_manage_access=user[9],
        group_ids=group_ids,
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency — raises 403 unless the authenticated user has role='admin'."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def require_access_manager(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency — raises 403 unless the user can tag documents
    with access grants (admin, or a reviewer delegated can_manage_access).
    Creating/managing groups themselves stays admin-only (require_admin)."""
    if not current_user.can_manage_document_access:
        raise HTTPException(status_code=403, detail="Access-management permission required")
    return current_user
