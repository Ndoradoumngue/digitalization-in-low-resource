"""
FastAPI backend for the SDAI Digitalization app.

Auth endpoints:
  POST /api/auth/login               — issue JWT, set httpOnly cookie
  POST /api/auth/logout              — clear cookie
  GET  /api/auth/me                  — return current user (or 401)

OCR endpoints (require auth):
  GET /api/documents                 — document list with OCR availability flag
  GET /api/documents/{filename}      — per-document OCR result (raw + preprocessed)
  GET /api/results                   — full _all_results.json

VLM endpoints (require auth):
  GET /api/vlm/documents             — document list with VLM availability flag
  GET /api/vlm/documents/{filename}  — per-document VLM extraction result
  GET /api/vlm/results               — full VLM results JSON

Image endpoints (require auth):
  GET /images/raw/{filename}         — original document image
  GET /images/preprocessed/{filename}

Run locally:
  CORS_ORIGINS=http://localhost:5173 uvicorn api_server:app --reload --port 8000
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import text

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from audit import client_ip, log_action
from auth import CurrentUser, get_current_user  # noqa: F401 — re-exported for Depends() callers
from rate_limit import limiter

# ── Config from environment ───────────────────────────────────────────────────

SECRET_KEY     = os.getenv("AUTH_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "8"))
SECURE_COOKIES = os.getenv("AUTH_SECURE_COOKIES", "false").lower() == "true"

# apps/api → apps → project root  (two levels up from this file)
# In Docker, PROJECT_ROOT is set to /app so volume mounts resolve correctly.
_HERE         = Path(__file__).parent
_PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(_HERE.parent.parent)))

DOCS_DIR         = Path(os.getenv("DOCS_DIR",        str(_PROJECT_ROOT / "documents" / "anonymized_docs")))
JSON_DIR         = Path(os.getenv("JSON_DIR",        str(_PROJECT_ROOT / "documents" / "ocr_results" / "json")))
PREPROCESS_DIR   = Path(os.getenv("PREPROCESS_DIR",  str(_PROJECT_ROOT / "documents" / "ocr_results" / "preprocessed")))
VLM_RESULTS_FILE = Path(os.getenv("VLM_RESULTS_FILE", str(JSON_DIR / "vlm_qwen25_results.json")))

# ── App + CORS ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from ingest_router import startup, shutdown
    from cache import init_cache
    await startup()
    await init_cache()
    yield
    await shutdown()


app = FastAPI(title="SDAI Digitalization API", lifespan=lifespan)
app.state.limiter = limiter

_cors_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_env:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_env.split(","),
        allow_credentials=True,          # required for httpOnly cookie exchange
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

# SlowAPI must come after CORS so the CORS headers survive on 429 responses
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    try:
        retry_after = int(exc.limit.multiples * exc.limit.granularity.SECONDS)
    except Exception:
        retry_after = 60
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded", "retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )

# ── Auth helpers ──────────────────────────────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _create_token(user_id: str, jti: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_H)
    return jwt.encode(
        {"sub": user_id, "exp": expire, "jti": jti},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=TOKEN_EXPIRE_H * 3600,
    )


# ── Auth endpoints ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email:    str
    password: str


@app.post("/api/auth/login", tags=["auth"], summary="Log in")
async def login(body: LoginRequest, request: Request, response: Response):
    """
    Authenticate with email and password.
    On success, sets an httpOnly `access_token` cookie containing a signed JWT.
    The cookie is required for all subsequent authenticated requests.
    """
    from ingest_router import _engine  # lazy — engine not ready at import time

    async with _engine().connect() as conn:
        row = await conn.execute(
            text(
                "SELECT id, email, full_name, role, hashed_password, is_active"
                " FROM sdai_users WHERE email = :e"
            ),
            {"e": body.email},
        )
        user = row.one_or_none()

    if user is None or not user[5] or not _pwd_ctx.verify(body.password, user[4]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    jti   = str(uuid.uuid4())
    token = _create_token(str(user[0]), jti)
    _set_auth_cookie(response, token)

    await log_action(
        action="user_login",
        user_id=str(user[0]),
        user_email=user[1],
        ip_address=client_ip(request),
    )

    return {"id": str(user[0]), "email": user[1], "full_name": user[2], "role": user[3]}


@app.post("/api/auth/logout", tags=["auth"], summary="Log out")
async def logout(
    request:      Request,
    response:     Response,
    access_token: Optional[str] = Cookie(default=None),
):
    """Blocklist the current JWT and clear the auth cookie."""
    user_id = None
    user_email: Optional[str] = None

    if access_token:
        try:
            from ingest_router import _engine  # lazy import

            token   = access_token.removeprefix("Bearer ")
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            jti     = payload.get("jti")
            exp     = payload.get("exp")
            user_id = payload.get("sub")
            if jti and exp:
                expired_at = datetime.fromtimestamp(exp, tz=timezone.utc)
                async with _engine().begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO sdai_token_blocklist (jti, expired_at)"
                            " VALUES (:jti, :exp) ON CONFLICT DO NOTHING"
                        ),
                        {"jti": jti, "exp": expired_at},
                    )
        except Exception:
            pass  # best-effort; always clear the cookie

    await log_action(
        action="user_logout",
        user_id=user_id,
        user_email=user_email,
        ip_address=client_ip(request),
    )

    response.delete_cookie(key="access_token", httponly=True, samesite="lax")
    return {"message": "logged out"}


@app.get("/api/auth/me", tags=["auth"], summary="Current user")
async def me(current_user: CurrentUser = Depends(get_current_user)):
    """Return the profile of the currently authenticated user, or 401 if not logged in."""
    return {
        "id":        current_user.id,
        "email":     current_user.email,
        "full_name": current_user.full_name,
        "role":      current_user.role,
    }


# ── Shared helpers ────────────────────────────────────────────────────────────

def _stem(filename: str) -> str:
    return Path(filename).stem


def _load_vlm_all() -> dict:
    if not VLM_RESULTS_FILE.exists():
        raise HTTPException(
            status_code=404,
            detail="VLM results file not found. Run vlm_ollama_test.py first.",
        )
    return json.loads(VLM_RESULTS_FILE.read_text(encoding="utf-8"))


# ── OCR endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/documents", tags=["ocr"], summary="List documents (OCR)")
def list_documents(current_user: str = Depends(get_current_user)):
    """
    Return all document filenames in the anonymized_docs directory with a
    `hasResult` flag indicating whether a pre-computed OCR JSON result exists.
    Used by the legacy OCR comparison dashboard tab.
    """
    images = sorted(
        f for f in os.listdir(DOCS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    return [
        {
            "filename": fname,
            "hasResult": (JSON_DIR / f"{_stem(fname)}_results.json").exists(),
        }
        for fname in images
    ]


@app.get("/api/documents/{filename}", tags=["ocr"], summary="OCR result for one document")
def get_document_result(filename: str, current_user: str = Depends(get_current_user)):
    """
    Return the full OCR result JSON for one document.
    Contains text, confidence, and processing time for each engine (Tesseract, EasyOCR, Surya)
    and each image variant (raw, preprocessed).
    """
    json_path = JSON_DIR / f"{_stem(filename)}_results.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="No OCR result for this document")
    return json.loads(json_path.read_text(encoding="utf-8"))


@app.get("/api/results", tags=["ocr"], summary="All OCR results")
def get_all_ocr_results(current_user: str = Depends(get_current_user)):
    """Return the combined _all_results.json file containing OCR results for every document."""
    all_path = JSON_DIR / "_all_results.json"
    if not all_path.exists():
        raise HTTPException(status_code=404, detail="_all_results.json not found")
    return json.loads(all_path.read_text(encoding="utf-8"))


# ── VLM endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/vlm/documents", tags=["vlm"], summary="List documents (VLM)")
def list_vlm_documents(current_user: str = Depends(get_current_user)):
    """
    Return all document filenames with a `hasResult` flag indicating whether a
    VLM extraction result exists. Used by the VLM Extraction dashboard tab.
    """
    all_results = _load_vlm_all()
    images = sorted(
        f for f in os.listdir(DOCS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    return [{"filename": fname, "hasResult": fname in all_results} for fname in images]


@app.get("/api/vlm/documents/{filename}", tags=["vlm"], summary="VLM result for one document")
def get_vlm_result(filename: str, current_user: str = Depends(get_current_user)):
    """
    Return the raw VLM extraction result for one document.
    The result is a discriminated union on the confidence tier
    (`high`, `medium`, `low`, or `failed`).
    """
    all_results = _load_vlm_all()
    if filename not in all_results:
        raise HTTPException(status_code=404, detail="No VLM result for this document")
    return all_results[filename]


@app.get("/api/vlm/results", tags=["vlm"], summary="All VLM results")
def get_all_vlm_results(current_user: str = Depends(get_current_user)):
    """Return all VLM extraction results as a filename-keyed map."""
    return _load_vlm_all()


# ── Image endpoints ───────────────────────────────────────────────────────────

@app.get("/images/raw/{filename}", tags=["images"], summary="Original document image")
def serve_raw_image(filename: str, current_user: str = Depends(get_current_user)):
    """Serve the original (un-preprocessed) document image from anonymized_docs/."""
    path = DOCS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Raw image not found")
    return FileResponse(path)


@app.get("/images/preprocessed/{filename}", tags=["images"], summary="Preprocessed document image")
def serve_preprocessed_image(filename: str, current_user: str = Depends(get_current_user)):
    """Serve the binarised/preprocessed image used as OCR engine input."""
    stem = _stem(filename)
    ext = Path(filename).suffix
    path = PREPROCESS_DIR / f"{stem}_preprocessed{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preprocessed image not found")
    return FileResponse(path)


# ── Ingest router ─────────────────────────────────────────────────────────────

from ingest_router import router as ingest_router  # noqa: E402
app.include_router(ingest_router)

# ── Documents DB router ───────────────────────────────────────────────────────

from documents_router import router as documents_router  # noqa: E402
app.include_router(documents_router)

# ── Review queue router ───────────────────────────────────────────────────────

from review_router import router as review_router  # noqa: E402
app.include_router(review_router)

# ── Admin router ──────────────────────────────────────────────────────────────

from admin_router import router as admin_router  # noqa: E402
app.include_router(admin_router)
