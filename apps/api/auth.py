"""
Shared JWT auth dependency - PostgreSQL-backed user lookup with role support.

Provides:
  get_current_user     - FastAPI dependency; returns CurrentUser or raises 401
  require_admin        - FastAPI dependency; returns CurrentUser or raises 403
  require_user         - any signed-in person (admin/reviewer/reader), never an API key
  require_contributor  - may upload/track ingestion (admin/reviewer/API key), never a reader
  require_reviewer     - may act on documents (admin/reviewer), never a reader or API key
  require_user_manager - may add / update / (de)activate accounts (admin, or can_manage_users)

Roles: admin, reviewer, reader (read-only), and the synthetic api_key.
Delegated flags (can_manage_access, can_edit_extraction,
can_manage_lifecycle) only take effect for reviewers - a reader stays
read-only whatever its flags say - except can_share, which a reader may
hold (sharing a document it can see changes nothing in the archive).
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request
from i18n import resolve_locale, t
from jose import JWTError, jwt

SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("AUTH_SECRET_KEY environment variable is not set")
if len(SECRET_KEY) < 32:
    # Anyone who guesses it can mint a session for any account.
    logging.getLogger(__name__).warning(
        "AUTH_SECRET_KEY is shorter than 32 characters - generate one with"
        " `openssl rand -hex 32` before going to production"
    )
ALGORITHM = "HS256"

# Prefix on every minted API key (see create_api_key.py) - lets
# get_current_user tell an external API key apart from any other bearer
# value a caller might put in the Authorization header without a DB round
# trip, and lets an admin recognize a key's purpose at a glance.
API_KEY_PREFIX = "sdai_"


@dataclass
class CurrentUser:
    id: str
    email: str
    full_name: str | None
    role: str
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    can_manage_access: bool
    can_edit_extraction: bool
    group_ids: list[str]
    locale: str
    can_manage_lifecycle: bool = False
    can_share: bool = False
    clearance: str = "internal"
    can_manage_users: bool = False
    must_change_password: bool = False
    password_changed_at: datetime | None = None
    job_title: str | None = None
    phone: str | None = None
    avatar_updated_at: datetime | None = None

    @property
    def is_reader(self) -> bool:
        return self.role == "reader"

    @property
    def can_manage_document_access(self) -> bool:
        """Admins can always tag documents with access grants; a reviewer
        needs the delegated can_manage_access flag."""
        return self.role == "admin" or (self.role == "reviewer" and self.can_manage_access)

    @property
    def can_edit_extraction_data(self) -> bool:
        """Admins can always edit extracted field data (manual entry for a
        crashed page, field corrections on approve, or correcting an
        already-filed document); a reviewer needs the delegated
        can_edit_extraction flag. Distinct from can_manage_access - one
        governs who can see a document, the other who can change its
        content."""
        return self.role == "admin" or (self.role == "reviewer" and self.can_edit_extraction)

    @property
    def can_manage_document_lifecycle(self) -> bool:
        """Archivist actions (retention, trash restore, destruction
        proposals, legal hold) - admin, or a reviewer delegated
        can_manage_lifecycle."""
        return self.role == "admin" or (self.role == "reviewer" and self.can_manage_lifecycle)

    @property
    def can_manage_user_accounts(self) -> bool:
        """Add / update / (de)activate accounts - admin, or a reviewer
        delegated can_manage_users (users_router enforces what a non-admin
        manager may grant). Deleting stays admin-only."""
        return self.role == "admin" or (self.role == "reviewer" and self.can_manage_users)

    @property
    def can_share_documents(self) -> bool:
        """Creating external share links - admin, or any person (reviewer
        or reader) delegated can_share. Never an API key."""
        return self.role == "admin" or (self.role in ("reviewer", "reader") and self.can_share)


# What a person whose password must be changed may still call (GED Phase 9):
# who am I, change it (logout needs no session).
PASSWORD_CHANGE_PATHS = frozenset({"/api/auth/me", "/api/auth/change-password"})


async def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    locale: str = Depends(resolve_locale),
) -> CurrentUser:
    """FastAPI dependency - raises 401 if the caller is neither a valid JWT
    cookie session nor a valid API key.

    An `Authorization: Bearer sdai_...` header is treated as an external API
    key (see create_api_key.py) rather than falling through to the cookie -
    a browser session never sends that header, so this is purely additive.
    Every other Authorization value (or none) is ignored here and the
    normal cookie flow runs unchanged."""
    if authorization and authorization.removeprefix("Bearer ").startswith(API_KEY_PREFIX):
        return await _get_current_user_from_api_key(authorization.removeprefix("Bearer "), locale)

    if not access_token:
        raise HTTPException(status_code=401, detail=t("auth.not_authenticated", locale))

    try:
        token = access_token.removeprefix("Bearer ")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        jti: str | None = payload.get("jti")
        issued_at = payload.get("iat")
        if not user_id:
            raise HTTPException(status_code=401, detail=t("auth.invalid_token", locale))
    except JWTError:
        raise HTTPException(status_code=401, detail=t("auth.invalid_or_expired_token", locale))

    # Lazy import avoids circular dependency (ingest_router imports auth at module level).
    from ingest_router import _engine
    from sqlalchemy import text

    async with _engine().connect() as conn:
        # Reject blocklisted tokens (e.g. after explicit logout)
        if jti:
            bl = await conn.execute(
                text("SELECT 1 FROM sdai_token_blocklist WHERE jti = :jti"),
                {"jti": jti},
            )
            if bl.one_or_none() is not None:
                raise HTTPException(status_code=401, detail=t("auth.token_revoked", locale))

        user = await load_user(conn, user_id, locale)

    if user is None:
        raise HTTPException(status_code=401, detail=t("auth.user_not_found_or_deactivated", locale))
    # A password set or reset since this session began ends it (sessions
    # from before iat was recorded too, once a password has changed).
    changed_at = user.password_changed_at
    if changed_at is not None and (
        not isinstance(issued_at, (int, float)) or issued_at < changed_at.timestamp()
    ):
        raise HTTPException(status_code=401, detail=t("auth.token_revoked", locale))
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_PATHS:
        raise HTTPException(
            status_code=403,
            detail=t("auth.password_change_required", locale),
            headers={"X-Password-Change-Required": "1"},
        )
    return user


async def load_user(conn, user_id: str, locale: str = "en") -> CurrentUser | None:
    """The person `user_id` as a CurrentUser (with groups), or None when
    they don't exist, are deactivated, or their tenant is. Also used to
    re-check, at the moment a share link is used, that whoever created it
    could still hand that document out."""
    from sqlalchemy import text

    row = await conn.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.role, u.is_active,"
            "       t.id AS tenant_id, t.slug AS tenant_slug, t.name AS tenant_name,"
            "       t.is_active AS tenant_is_active, u.can_manage_access,"
            "       u.can_edit_extraction, u.can_manage_lifecycle, u.can_share, u.clearance,"
            "       u.can_manage_users, u.must_change_password, u.deleted_at,"
            "       u.password_changed_at, u.job_title, u.phone, u.avatar_updated_at"
            " FROM sdai_users u"
            " JOIN sdai_tenants t ON t.id = u.tenant_id"
            " WHERE u.id = CAST(:id AS uuid)"
        ),
        {"id": user_id},
    )
    user = row.one_or_none()
    if user is None or not user[4] or not user[8] or user[16] is not None:
        return None

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
        can_edit_extraction=user[10],
        group_ids=group_ids,
        locale=locale,
        can_manage_lifecycle=user[11],
        can_share=user[12],
        clearance=user[13],
        can_manage_users=user[14],
        must_change_password=user[15],
        password_changed_at=user[17],
        job_title=user[18],
        phone=user[19],
        avatar_updated_at=user[20],
    )


async def _get_current_user_from_api_key(api_key: str, locale: str) -> CurrentUser:
    """Resolves an external API key (see create_api_key.py) to a
    CurrentUser scoped to that key's tenant. Only a hash of the key is ever
    stored, so lookup is by exact hash match - same reasoning as
    sdai_users.hashed_password, except a high-entropy generated secret
    (unlike a human-chosen password) doesn't need a slow salted hash to
    resist brute-forcing.

    role='api_key' deliberately never satisfies require_admin,
    require_user, require_reviewer, require_access_manager, or
    require_extraction_editor - an API key can only ever reach
    require_contributor endpoints (upload + ingestion status polling),
    never the archive itself or admin/review actions."""
    hashed_key = hashlib.sha256(api_key.encode()).hexdigest()

    # Lazy import - same circular-dependency reasoning as get_current_user's
    # own `from ingest_router import _engine` above.
    from ingest_router import _engine
    from sqlalchemy import text

    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT k.id, k.name, k.is_active, k.expires_at,"
                "       t.id AS tenant_id, t.slug AS tenant_slug, t.name AS tenant_name,"
                "       t.is_active AS tenant_is_active"
                " FROM sdai_api_keys k"
                " JOIN sdai_tenants t ON t.id = k.tenant_id"
                " WHERE k.hashed_key = :hashed_key"
            ),
            {"hashed_key": hashed_key},
        )
        key_row = row.one_or_none()

    if key_row is None or not key_row[2] or not key_row[7]:
        raise HTTPException(status_code=401, detail=t("auth.invalid_api_key", locale))
    if key_row[3] is not None and key_row[3] < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail=t("auth.invalid_api_key", locale))

    return CurrentUser(
        id=str(key_row[0]),
        email=f"api-key:{key_row[1]}",
        full_name=None,
        role="api_key",
        tenant_id=str(key_row[4]),
        tenant_slug=key_row[5],
        tenant_name=key_row[6],
        can_manage_access=False,
        can_edit_extraction=False,
        group_ids=[],
        locale=locale,
        # Never sees archive content (require_user rejects it) - the
        # lowest clearance keeps that true even if a route forgets.
        clearance="public",
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - raises 403 unless the authenticated user has role='admin'."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail=t("auth.admin_required", current_user.locale))
    return current_user


async def require_access_manager(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - raises 403 unless the user can tag documents
    with access grants (admin, or a reviewer delegated can_manage_access).
    Creating/managing groups themselves stays admin-only (require_admin)."""
    if not current_user.can_manage_document_access:
        raise HTTPException(
            status_code=403, detail=t("auth.access_manager_required", current_user.locale)
        )
    return current_user


async def require_extraction_editor(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - raises 403 unless the user can edit extracted
    field data (admin, or a reviewer delegated can_edit_extraction).
    Covers manual entry for a crashed page, field corrections made while
    approving a review_required document, and editing an already-filed
    document's fields - everywhere extraction content can be changed."""
    if not current_user.can_edit_extraction_data:
        raise HTTPException(
            status_code=403, detail=t("auth.extraction_editor_required", current_user.locale)
        )
    return current_user


async def require_user_manager(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - may add / update / (de)activate accounts
    (admin, or a reviewer delegated can_manage_users)."""
    if not current_user.can_manage_user_accounts:
        raise HTTPException(
            status_code=403, detail=t("auth.user_manager_required", current_user.locale)
        )
    return current_user


async def require_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - any signed-in person (admin, reviewer or
    reader). Rejects API keys, which are upload-only and never browse the
    archive."""
    if current_user.role not in ("admin", "reviewer", "reader"):
        raise HTTPException(status_code=403, detail=t("auth.user_required", current_user.locale))
    return current_user


async def require_contributor(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - may upload documents and follow their
    ingestion (admin, reviewer, or an API key). Rejects readers."""
    if current_user.role not in ("admin", "reviewer", "api_key"):
        raise HTTPException(
            status_code=403, detail=t("auth.contributor_required", current_user.locale)
        )
    return current_user


async def require_reviewer(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """FastAPI dependency - may act on documents (review, flag, retry,
    file into a series, link, steer page extraction): admin or reviewer.
    Rejects readers and API keys."""
    if current_user.role not in ("admin", "reviewer"):
        raise HTTPException(status_code=403, detail=t("auth.reviewer_required", current_user.locale))
    return current_user
