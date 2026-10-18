"""
My profile - what any signed-in person may change about themselves.

Endpoints (admin, reviewer or reader - never an API key):
  PATCH  /api/account/profile     - full name, job title, phone
  PUT    /api/account/avatar      - set my picture (multipart `file`)
  DELETE /api/account/avatar      - remove it
  GET    /api/users/{id}/avatar   - someone's picture (same tenant only)

Rules:
- The e-mail address, role, clearance and permissions are not editable
  here: they decide who signs in and what they see, so they stay with
  the user managers (users_router).
- A picture is decoded and re-encoded as a 256x256 JPEG: whatever was
  uploaded (EXIF with a GPS position, a polyglot file, an animation) is
  never served back, only the pixels. It is kept in sdai_users.avatar as
  base64 so the sync ships it with the row; the URL carries
  avatar_updated_at so a browser cache never shows an old picture.
"""

from __future__ import annotations

import base64
import binascii
import io
from datetime import datetime, timezone

from audit import client_ip, log_action
from auth import CurrentUser, require_user
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from i18n import t
from ingest_router import _engine
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from rate_limit import limiter
from sqlalchemy import text

router = APIRouter(tags=["profile"])

AVATAR_SIZE = 256
MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
# Refuse before decoding: a small file can declare a huge canvas.
MAX_AVATAR_PIXELS = 40_000_000
AVATAR_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")


def avatar_url(user_id: str, updated_at: datetime | None) -> str | None:
    """Where the picture of `user_id` is served, or None when they have none."""
    if updated_at is None:
        return None
    return f"/api/users/{user_id}/avatar?v={int(updated_at.timestamp())}"


def _http(status: int, key: str, locale: str, **params) -> HTTPException:
    return HTTPException(status_code=status, detail=t(key, locale, **params))


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def normalize_avatar(content: bytes, locale: str) -> bytes:
    """The uploaded image as a square AVATAR_SIZE JPEG without metadata,
    or 422 when it isn't an image we accept."""
    try:
        with Image.open(io.BytesIO(content)) as img:
            if img.format not in AVATAR_FORMATS:
                raise _http(422, "profile.avatar_invalid", locale)
            width, height = img.size
            if width * height > MAX_AVATAR_PIXELS:
                raise _http(422, "profile.avatar_invalid", locale)
            img.seek(0)  # first frame of an animation
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                img = Image.new("RGB", rgba.size, (255, 255, 255))
                img.paste(rgba, mask=rgba.getchannel("A"))
            else:
                img = img.convert("RGB")
            img = ImageOps.fit(
                img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS
            )
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise _http(422, "profile.avatar_invalid", locale) from None


class ProfileBody(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    job_title: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)


@router.patch("/api/account/profile", summary="Change my profile")
async def update_profile(
    body: ProfileBody,
    request: Request,
    current_user: CurrentUser = Depends(require_user),
):
    """Only the fields sent change; an empty string clears one."""
    changes = {k: _clean(v) for k, v in body.model_dump(exclude_unset=True).items()}
    if changes:
        sets = ", ".join(f"{col} = :{col}" for col in changes)
        async with _engine().begin() as conn:
            await conn.execute(
                text(f"UPDATE sdai_users SET {sets} WHERE id = CAST(:id AS uuid)"),
                {**changes, "id": current_user.id},
            )
        await log_action(
            action="profile_updated",
            user_id=current_user.id,
            user_email=current_user.email,
            details={"fields": sorted(changes)},
            ip_address=client_ip(request),
        )
    profile = {
        "full_name": current_user.full_name,
        "job_title": current_user.job_title,
        "phone": current_user.phone,
        **changes,
    }
    return profile


@router.put("/api/account/avatar", summary="Set my profile picture")
@limiter.limit("20/minute")
async def put_avatar(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_user),
):
    locale = current_user.locale
    content = await file.read(MAX_AVATAR_UPLOAD_BYTES + 1)
    if len(content) > MAX_AVATAR_UPLOAD_BYTES:
        raise _http(
            413,
            "ingest.file_too_large",
            locale,
            filename=file.filename or "image",
            max_mb=MAX_AVATAR_UPLOAD_BYTES // (1024 * 1024),
        )
    jpeg = normalize_avatar(content, locale)
    now = datetime.now(timezone.utc)
    async with _engine().begin() as conn:
        await conn.execute(
            text(
                "UPDATE sdai_users SET avatar = :a, avatar_updated_at = :at"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"a": base64.b64encode(jpeg).decode(), "at": now, "id": current_user.id},
        )
    await log_action(
        action="avatar_changed",
        user_id=current_user.id,
        user_email=current_user.email,
        ip_address=client_ip(request),
    )
    return {"avatar_url": avatar_url(current_user.id, now)}


@router.delete("/api/account/avatar", summary="Remove my profile picture")
async def delete_avatar(
    request: Request,
    current_user: CurrentUser = Depends(require_user),
):
    async with _engine().begin() as conn:
        await conn.execute(
            text(
                "UPDATE sdai_users SET avatar = NULL, avatar_updated_at = NULL"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"id": current_user.id},
        )
    await log_action(
        action="avatar_removed",
        user_id=current_user.id,
        user_email=current_user.email,
        ip_address=client_ip(request),
    )
    return {"avatar_url": None}


@router.get("/api/users/{user_id}/avatar", summary="Someone's profile picture")
async def get_avatar(
    user_id: str,
    current_user: CurrentUser = Depends(require_user),
):
    """Colleagues of the same tenant only; a deleted account has none."""
    async with _engine().connect() as conn:
        data = (
            await conn.execute(
                text(
                    "SELECT avatar FROM sdai_users WHERE id = CAST(:id AS uuid)"
                    " AND tenant_id = CAST(:t AS uuid) AND deleted_at IS NULL"
                ),
                {"id": user_id, "t": current_user.tenant_id},
            )
        ).scalar()
    try:
        jpeg = base64.b64decode(data, validate=True) if data else None
    except (binascii.Error, ValueError):
        jpeg = None
    if not jpeg:
        raise _http(404, "common.not_found", current_user.locale)
    # Versioned URL (?v=) - safe for the browser to keep, never a shared cache.
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
