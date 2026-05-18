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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Config from environment ───────────────────────────────────────────────────

SECRET_KEY     = os.getenv("AUTH_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "8"))
SECURE_COOKIES = os.getenv("AUTH_SECURE_COOKIES", "false").lower() == "true"

AUTH_USERNAME  = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD  = os.getenv("AUTH_PASSWORD", "changeme")

BASE           = Path(__file__).parent
DOCS_DIR       = BASE / "documents" / "anonymized_docs"
JSON_DIR       = BASE / "documents" / "ocr_results" / "json"
PREPROCESS_DIR = BASE / "documents" / "ocr_results" / "preprocessed"
VLM_RESULTS_FILE = JSON_DIR / "vlm_qwen25_results.json"

# ── App + CORS ────────────────────────────────────────────────────────────────

app = FastAPI(title="SDAI Digitalization API")

_cors_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_env:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_env.split(","),
        allow_credentials=True,          # required for httpOnly cookie exchange
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

# ── Auth helpers ──────────────────────────────────────────────────────────────

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_hashed_password = _pwd_ctx.hash(AUTH_PASSWORD)


def _verify_password(plain: str) -> bool:
    return _pwd_ctx.verify(plain, _hashed_password)


def _create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_H)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=TOKEN_EXPIRE_H * 3600,
    )


def get_current_user(access_token: Optional[str] = Cookie(default=None)) -> str:
    """FastAPI dependency — raises 401 if the JWT cookie is missing or invalid."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        token = access_token.removeprefix("Bearer ")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── Auth endpoints ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginRequest, response: Response):
    if body.username != AUTH_USERNAME or not _verify_password(body.password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = _create_token(body.username)
    _set_auth_cookie(response, token)
    return {"username": body.username}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(key="access_token", httponly=True, samesite="lax")
    return {"message": "logged out"}


@app.get("/api/auth/me")
def me(current_user: str = Depends(get_current_user)):
    return {"username": current_user}


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

@app.get("/api/documents")
def list_documents(current_user: str = Depends(get_current_user)):
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


@app.get("/api/documents/{filename}")
def get_document_result(filename: str, current_user: str = Depends(get_current_user)):
    json_path = JSON_DIR / f"{_stem(filename)}_results.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="No OCR result for this document")
    return json.loads(json_path.read_text(encoding="utf-8"))


@app.get("/api/results")
def get_all_ocr_results(current_user: str = Depends(get_current_user)):
    all_path = JSON_DIR / "_all_results.json"
    if not all_path.exists():
        raise HTTPException(status_code=404, detail="_all_results.json not found")
    return json.loads(all_path.read_text(encoding="utf-8"))


# ── VLM endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/vlm/documents")
def list_vlm_documents(current_user: str = Depends(get_current_user)):
    all_results = _load_vlm_all()
    images = sorted(
        f for f in os.listdir(DOCS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    return [{"filename": fname, "hasResult": fname in all_results} for fname in images]


@app.get("/api/vlm/documents/{filename}")
def get_vlm_result(filename: str, current_user: str = Depends(get_current_user)):
    all_results = _load_vlm_all()
    if filename not in all_results:
        raise HTTPException(status_code=404, detail="No VLM result for this document")
    return all_results[filename]


@app.get("/api/vlm/results")
def get_all_vlm_results(current_user: str = Depends(get_current_user)):
    return _load_vlm_all()


# ── Image endpoints ───────────────────────────────────────────────────────────

@app.get("/images/raw/{filename}")
def serve_raw_image(filename: str, current_user: str = Depends(get_current_user)):
    path = DOCS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Raw image not found")
    return FileResponse(path)


@app.get("/images/preprocessed/{filename}")
def serve_preprocessed_image(filename: str, current_user: str = Depends(get_current_user)):
    stem = _stem(filename)
    ext = Path(filename).suffix
    path = PREPROCESS_DIR / f"{stem}_preprocessed{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preprocessed image not found")
    return FileResponse(path)
