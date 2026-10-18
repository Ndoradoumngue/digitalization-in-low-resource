"""
User management (GED Phase 9) - accounts of the caller's tenant.

Endpoints (admin, or a reviewer delegated can_manage_users, except delete):
  GET    /api/admin/users                        - accounts (never deleted ones), with groups
  POST   /api/admin/users                        - create, with a temporary password
  PATCH  /api/admin/users/{id}                   - name, role, clearance, delegated permissions
  POST   /api/admin/users/{id}/deactivate        - block sign-in, end sessions
  POST   /api/admin/users/{id}/activate          - unblock
  POST   /api/admin/users/{id}/reset-password    - new temporary password
  DELETE /api/admin/users/{id}                   - admin only: soft delete

Rules:
- A non-admin manager never gives more than they hold: no admin role, no
  clearance above their own, no delegated permission they lack - and they
  can't act on an admin or on someone cleared above them.
- Nobody changes their own role / clearance / permissions here, nor
  deactivates or deletes themselves; the last active admin of the tenant
  can't be demoted, deactivated or deleted.
- A new or reset password is temporary: the person must choose their own at
  the next sign-in (auth.get_current_user refuses everything else until
  then), and every session opened before it ends.
- Deleting hides the account everywhere and frees its e-mail address: the
  row stays (deleted_at), its address and name move to deleted_email /
  deleted_full_name and `email` becomes '<id>@deleted.invalid' - which
  every screen shows as "user no longer in system". Its group memberships,
  document grants and check-outs go; what it did stays attributed to it.
- Group membership stays admin-only (admin_router): it grants document
  access, which a user manager doesn't hand out.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import bcrypt as _bcrypt
from audit import client_ip, log_action
from auth import CurrentUser, require_admin, require_user_manager
from fastapi import APIRouter, Depends, HTTPException, Request
from i18n import t
from ingest_router import _engine
from profile_router import avatar_url
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text

router = APIRouter(prefix="/api/admin/users", tags=["users"])

# Reserved by RFC 2606 - never a real mailbox. The frontend shows any value
# of this form as "user no longer in system" (packages/api-client).
DELETED_EMAIL_DOMAIN = "deleted.invalid"

FLAGS = (
    "can_manage_access",
    "can_edit_extraction",
    "can_manage_lifecycle",
    "can_share",
    "can_manage_users",
)
CLEARANCES = ("public", "internal", "confidential", "secret")
ROLES = ("admin", "reviewer", "reader")
MIN_PASSWORD_LENGTH = 8
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_USER_COLS = """
    u.id::text AS id, u.email, u.full_name, u.role, u.clearance,
    u.can_manage_access, u.can_edit_extraction, u.can_manage_lifecycle, u.can_share,
    u.can_manage_users, COALESCE(u.is_active, true) AS is_active, u.must_change_password,
    u.created_at, u.last_login_at, u.job_title, u.avatar_updated_at,
    COALESCE(ARRAY_AGG(ug.group_id::text) FILTER (WHERE ug.group_id IS NOT NULL), '{}') AS group_ids
"""


def tombstone_email(user_id: str) -> str:
    return f"{user_id}@{DELETED_EMAIL_DOMAIN}"


def password_problem(password: str, email: str | None = None) -> str | None:
    """The i18n key of what's wrong with a new password, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "users.password_too_short"
    if email and password.strip().lower() in (
        email.lower(),
        email.split("@")[0].lower(),
    ):
        return "users.password_like_email"
    return None


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _serialize(row) -> dict:
    data = dict(row)
    data["avatar_url"] = avatar_url(data["id"], data.pop("avatar_updated_at"))
    for key in ("created_at", "last_login_at"):
        data[key] = _iso(data[key])
    return data


def _http(status: int, key: str, locale: str, **params) -> HTTPException:
    return HTTPException(status_code=status, detail=t(key, locale, **params))


# ── Bodies ───────────────────────────────────────────────────────────────────


class _Permissions(BaseModel):
    can_manage_access: bool | None = None
    can_edit_extraction: bool | None = None
    can_manage_lifecycle: bool | None = None
    can_share: bool | None = None
    can_manage_users: bool | None = None


class CreateUserBody(_Permissions):
    email: str = Field(..., min_length=3, max_length=254)
    full_name: str | None = Field(None, max_length=200)
    role: str = Field("reviewer", pattern="^(admin|reviewer|reader)$")
    clearance: str = Field(
        "internal", pattern="^(public|internal|confidential|secret)$"
    )
    temporary_password: str = Field(..., max_length=200)


class UpdateUserBody(_Permissions):
    full_name: str | None = Field(None, max_length=200)
    role: str | None = Field(None, pattern="^(admin|reviewer|reader)$")
    clearance: str | None = Field(
        None, pattern="^(public|internal|confidential|secret)$"
    )


class ResetPasswordBody(BaseModel):
    temporary_password: str = Field(..., max_length=200)


# ── Rules ────────────────────────────────────────────────────────────────────


def _holds(manager: CurrentUser, flag: str) -> bool:
    return manager.role == "admin" or bool(getattr(manager, flag, False))


def _check_grants(
    manager: CurrentUser, role: str | None, clearance: str | None, flags: dict
) -> None:
    """403 when a non-admin manager would hand out more than they hold."""
    if manager.role == "admin":
        return
    locale = manager.locale
    if role == "admin":
        raise _http(403, "users.cannot_grant_admin", locale)
    if clearance and CLEARANCES.index(clearance) > CLEARANCES.index(manager.clearance):
        raise _http(403, "users.clearance_above_yours", locale)
    for flag, value in flags.items():
        if value is not None and not _holds(manager, flag):
            raise _http(403, "users.cannot_grant_permission", locale)


def _check_manageable(manager: CurrentUser, target) -> None:
    """403 when a non-admin manager acts on an admin or on someone cleared
    above them."""
    if manager.role == "admin":
        return
    if target["role"] == "admin":
        raise _http(403, "users.cannot_manage_admin", manager.locale)
    if CLEARANCES.index(target["clearance"]) > CLEARANCES.index(manager.clearance):
        raise _http(403, "users.clearance_above_yours", manager.locale)


async def _target(conn, user_id: str, manager: CurrentUser):
    """The (non-deleted) account of the manager's tenant, row-locked; 404
    otherwise."""
    row = (
        (
            await conn.execute(
                text("""
                    SELECT id::text AS id, email, full_name, role, clearance,
                           COALESCE(is_active, true) AS is_active
                    FROM sdai_users
                    WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)
                      AND deleted_at IS NULL
                    FOR UPDATE
                """),
                {"id": user_id, "tid": manager.tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise _http(404, "common.user_not_found", manager.locale)
    return row


async def _check_last_admin(conn, target, manager: CurrentUser) -> None:
    """409 when `target` is the tenant's last active admin."""
    if target["role"] != "admin" or not target["is_active"]:
        return
    others = (
        await conn.execute(
            text("""
                SELECT count(*) FROM sdai_users
                WHERE tenant_id = CAST(:tid AS uuid) AND role = 'admin'
                  AND COALESCE(is_active, true) AND deleted_at IS NULL
                  AND id <> CAST(:id AS uuid)
            """),
            {"tid": manager.tenant_id, "id": target["id"]},
        )
    ).scalar()
    if not others:
        raise _http(409, "users.last_admin", manager.locale)


async def _end_activity(conn, user_id: str, tenant_slug: str) -> None:
    """What an account that can no longer sign in leaves behind on this node:
    its check-outs are released and its unsent e-mails dropped (each node's
    e-mail worker also skips inactive accounts)."""
    from documents_router import _get_tables_columns

    for table, cols in (await _get_tables_columns(conn, tenant_slug)).items():
        if "locked_by" in cols:
            await conn.execute(
                text(
                    f'UPDATE "{table}" SET locked_by = NULL, locked_until = NULL'
                    " WHERE locked_by = CAST(:u AS uuid)"
                ),
                {"u": user_id},
            )
    await conn.execute(
        text(
            "UPDATE sdai_email_outbox SET status = 'failed', last_error = 'account deactivated'"
            " WHERE user_id = CAST(:u AS uuid) AND status IN ('pending', 'sending')"
        ),
        {"u": user_id},
    )


async def _load(conn, user_id: str) -> dict:
    row = (
        (
            await conn.execute(
                text(f"""
                    SELECT {_USER_COLS} FROM sdai_users u
                    LEFT JOIN sdai_user_groups ug ON ug.user_id = u.id
                    WHERE u.id = CAST(:id AS uuid) GROUP BY u.id
                """),
                {"id": user_id},
            )
        )
        .mappings()
        .one()
    )
    return _serialize(row)


async def _audit(
    request: Request, manager: CurrentUser, action: str, target_id: str, **details
):
    await log_action(
        action=action,
        user_id=manager.id,
        user_email=manager.email,
        details={"target_user_id": target_id, **details},
        ip_address=client_ip(request),
    )


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", summary="List users")
async def list_users(current_user: CurrentUser = Depends(require_user_manager)):
    """This tenant's accounts - active and deactivated, never deleted ones -
    with their group memberships."""
    async with _engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
                        SELECT {_USER_COLS} FROM sdai_users u
                        LEFT JOIN sdai_user_groups ug ON ug.user_id = u.id
                        WHERE u.tenant_id = CAST(:tid AS uuid) AND u.deleted_at IS NULL
                        GROUP BY u.id
                        ORDER BY lower(COALESCE(u.full_name, u.email))
                    """),
                    {"tid": current_user.tenant_id},
                )
            )
            .mappings()
            .all()
        )
    return [_serialize(r) for r in rows]


@router.post("", summary="Create a user", status_code=201)
@limiter.limit("30/minute")
async def create_user(
    request: Request,
    body: CreateUserBody,
    current_user: CurrentUser = Depends(require_user_manager),
):
    locale = current_user.locale
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email) or email.endswith("@" + DELETED_EMAIL_DOMAIN):
        raise _http(422, "users.invalid_email", locale)
    flags = {f: getattr(body, f) for f in FLAGS}
    _check_grants(current_user, body.role, body.clearance, flags)
    problem = password_problem(body.temporary_password, email)
    if problem:
        raise _http(422, problem, locale)
    hashed = _bcrypt.hashpw(
        body.temporary_password.encode(), _bcrypt.gensalt()
    ).decode()
    async with _engine().begin() as conn:
        existing = (
            await conn.execute(
                text(
                    "SELECT COALESCE(is_active, true) FROM sdai_users WHERE lower(email) = :e"
                ),
                {"e": email},
            )
        ).one_or_none()
        if existing is not None:
            raise _http(
                409,
                "users.email_taken" if existing[0] else "users.email_taken_inactive",
                locale,
            )
        user_id = (
            await conn.execute(
                text("""
                    INSERT INTO sdai_users
                        (email, hashed_password, full_name, role, tenant_id, clearance,
                         can_manage_access, can_edit_extraction, can_manage_lifecycle, can_share,
                         can_manage_users, must_change_password, password_changed_at)
                    VALUES (:email, :hashed, :name, :role, CAST(:tid AS uuid), :clearance,
                            :can_manage_access, :can_edit_extraction, :can_manage_lifecycle,
                            :can_share, :can_manage_users, true, :now)
                    RETURNING id::text
                """),
                {
                    "email": email,
                    "hashed": hashed,
                    "name": (body.full_name or "").strip() or None,
                    "role": body.role,
                    "tid": current_user.tenant_id,
                    "clearance": body.clearance,
                    "now": datetime.now(timezone.utc),
                    **{f: bool(v) for f, v in flags.items()},
                },
            )
        ).scalar()
        user = await _load(conn, user_id)
    await _audit(
        request,
        current_user,
        "user_created",
        user_id,
        role=body.role,
        clearance=body.clearance,
        **{f: True for f, v in flags.items() if v},
    )
    return user


@router.patch("/{user_id}", summary="Update a user")
@limiter.limit("60/minute")
async def update_user(
    request: Request,
    user_id: str,
    body: UpdateUserBody,
    current_user: CurrentUser = Depends(require_user_manager),
):
    """Partial update: only the fields sent change."""
    locale = current_user.locale
    changes = body.model_dump(exclude_none=True)
    if "full_name" in changes:
        changes["full_name"] = changes["full_name"].strip() or None
    if not changes:
        raise _http(422, "admin.no_fields_to_update", locale)
    async with _engine().begin() as conn:
        target = await _target(conn, user_id, current_user)
        _check_manageable(current_user, target)
        if target["id"] == current_user.id and set(changes) - {"full_name"}:
            raise _http(409, "users.not_your_own_rights", locale)
        _check_grants(
            current_user,
            changes.get("role"),
            changes.get("clearance"),
            {f: changes.get(f) for f in FLAGS},
        )
        if changes.get("role", "admin") != "admin":
            await _check_last_admin(conn, target, current_user)
        await conn.execute(
            text(
                "UPDATE sdai_users SET "
                + ", ".join(f"{field} = :{field}" for field in changes)
                + " WHERE id = CAST(:id AS uuid)"
            ),
            {**changes, "id": user_id},
        )
        user = await _load(conn, user_id)
    await _audit(request, current_user, "user_updated", user_id, **changes)
    return user


@router.post("/{user_id}/deactivate", summary="Deactivate a user")
@limiter.limit("60/minute")
async def deactivate_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_user_manager),
):
    """Sign-in refused and open sessions rejected on their next request; the
    account and everything it did stay. Its share links stop working too."""
    async with _engine().begin() as conn:
        target = await _target(conn, user_id, current_user)
        _check_manageable(current_user, target)
        if target["id"] == current_user.id:
            raise _http(409, "users.not_yourself", current_user.locale)
        await _check_last_admin(conn, target, current_user)
        await conn.execute(
            text(
                "UPDATE sdai_users SET is_active = false WHERE id = CAST(:id AS uuid)"
            ),
            {"id": user_id},
        )
        await _end_activity(conn, user_id, current_user.tenant_slug)
        user = await _load(conn, user_id)
    if target["is_active"]:
        await _audit(request, current_user, "user_deactivated", user_id)
    return user


@router.post("/{user_id}/activate", summary="Reactivate a user")
@limiter.limit("60/minute")
async def activate_user(
    request: Request,
    user_id: str,
    current_user: CurrentUser = Depends(require_user_manager),
):
    async with _engine().begin() as conn:
        target = await _target(conn, user_id, current_user)
        _check_manageable(current_user, target)
        await conn.execute(
            text("UPDATE sdai_users SET is_active = true WHERE id = CAST(:id AS uuid)"),
            {"id": user_id},
        )
        user = await _load(conn, user_id)
    if not target["is_active"]:
        await _audit(request, current_user, "user_reactivated", user_id)
    return user


@router.post("/{user_id}/reset-password", summary="Set a new temporary password")
@limiter.limit("20/minute")
async def reset_password(
    request: Request,
    user_id: str,
    body: ResetPasswordBody,
    current_user: CurrentUser = Depends(require_user_manager),
):
    """The person signs in with it and must choose their own; their open
    sessions end now."""
    locale = current_user.locale
    async with _engine().begin() as conn:
        target = await _target(conn, user_id, current_user)
        _check_manageable(current_user, target)
        if target["id"] == current_user.id:
            raise _http(409, "users.use_change_password", locale)
        problem = password_problem(body.temporary_password, target["email"])
        if problem:
            raise _http(422, problem, locale)
        hashed = _bcrypt.hashpw(
            body.temporary_password.encode(), _bcrypt.gensalt()
        ).decode()
        await conn.execute(
            text(
                "UPDATE sdai_users SET hashed_password = :h, must_change_password = true,"
                " password_changed_at = :now WHERE id = CAST(:id AS uuid)"
            ),
            {"h": hashed, "id": user_id, "now": datetime.now(timezone.utc)},
        )
        user = await _load(conn, user_id)
    await _audit(request, current_user, "password_reset", user_id)
    return user


@router.delete("/{user_id}", summary="Delete a user (admin)")
@limiter.limit("30/minute")
async def delete_user(
    request: Request, user_id: str, current_user: CurrentUser = Depends(require_admin)
):
    """Soft delete: hidden everywhere, shown as "user no longer in system"
    where it is referenced, e-mail address freed. The row and its history
    stay in the database (deleted_email / deleted_full_name)."""
    async with _engine().begin() as conn:
        target = await _target(conn, user_id, current_user)
        if target["id"] == current_user.id:
            raise _http(409, "users.not_yourself", current_user.locale)
        await _check_last_admin(conn, target, current_user)
        await conn.execute(
            text("""
                UPDATE sdai_users
                SET deleted_at = now(), deleted_by = CAST(:by AS uuid),
                    deleted_email = email, deleted_full_name = full_name,
                    email = :tombstone, full_name = NULL,
                    job_title = NULL, phone = NULL, avatar = NULL, avatar_updated_at = NULL,
                    is_active = false, must_change_password = false
                WHERE id = CAST(:id AS uuid)
            """),
            {
                "id": user_id,
                "by": current_user.id,
                "tombstone": tombstone_email(user_id),
            },
        )
        await conn.execute(
            text("DELETE FROM sdai_user_groups WHERE user_id = CAST(:id AS uuid)"),
            {"id": user_id},
        )
        await conn.execute(
            text(
                "DELETE FROM sdai_document_access WHERE grantee_type = 'user'"
                " AND grantee_id = CAST(:id AS uuid)"
            ),
            {"id": user_id},
        )
        await _end_activity(conn, user_id, current_user.tenant_slug)
    await _audit(request, current_user, "user_deleted", user_id)
    return {"deleted": True}
