"""
FastAPI backend for the SDAI Digitalization dashboard.

OCR endpoints:
  GET /api/documents                    — document list with OCR result availability
  GET /api/documents/{filename}         — per-document OCR result (raw + preprocessed)
  GET /api/results                      — full _all_results.json

VLM endpoints:
  GET /api/vlm/documents                — document list with VLM result availability
  GET /api/vlm/documents/{filename}     — per-document VLM extraction result
  GET /api/vlm/results                  — full VLM results JSON (Ollama model)

Image endpoints:
  GET /images/raw/{filename}            — original document image
  GET /images/preprocessed/{filename}  — preprocessed (binarised) image

Run with:
  uvicorn api_server:app --reload --port 8000
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

BASE = Path(__file__).parent
DOCS_DIR      = BASE / "documents" / "anonymized_docs"
JSON_DIR      = BASE / "documents" / "ocr_results" / "json"
PREPROCESS_DIR = BASE / "documents" / "ocr_results" / "preprocessed"

# VLM result files produced by vlm_ollama_test.py / vlm_local_test.py
VLM_RESULTS_FILE = JSON_DIR / "vlm_qwen25_results.json"

app = FastAPI(title="SDAI Digitalization API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _stem(filename: str) -> str:
    return Path(filename).stem


def _load_vlm_all() -> dict:
    if not VLM_RESULTS_FILE.exists():
        raise HTTPException(status_code=404, detail="VLM results file not found. Run vlm_ollama_test.py first.")
    return json.loads(VLM_RESULTS_FILE.read_text(encoding="utf-8"))


# ── OCR endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/documents")
def list_documents():
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
def get_document_result(filename: str):
    json_path = JSON_DIR / f"{_stem(filename)}_results.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="No OCR result for this document")
    return json.loads(json_path.read_text(encoding="utf-8"))


@app.get("/api/results")
def get_all_ocr_results():
    all_path = JSON_DIR / "_all_results.json"
    if not all_path.exists():
        raise HTTPException(status_code=404, detail="_all_results.json not found")
    return json.loads(all_path.read_text(encoding="utf-8"))


# ── VLM endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/vlm/documents")
def list_vlm_documents():
    """List documents with a flag indicating whether a VLM result exists."""
    all_results = _load_vlm_all()
    images = sorted(
        f for f in os.listdir(DOCS_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )
    return [
        {"filename": fname, "hasResult": fname in all_results}
        for fname in images
    ]


@app.get("/api/vlm/documents/{filename}")
def get_vlm_result(filename: str):
    all_results = _load_vlm_all()
    if filename not in all_results:
        raise HTTPException(status_code=404, detail="No VLM result for this document")
    return all_results[filename]


@app.get("/api/vlm/results")
def get_all_vlm_results():
    return _load_vlm_all()


# ── Image endpoints ───────────────────────────────────────────────────────────

@app.get("/images/raw/{filename}")
def serve_raw_image(filename: str):
    path = DOCS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Raw image not found")
    return FileResponse(path)


@app.get("/images/preprocessed/{filename}")
def serve_preprocessed_image(filename: str):
    stem = _stem(filename)
    ext = Path(filename).suffix
    path = PREPROCESS_DIR / f"{stem}_preprocessed{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preprocessed image not found")
    return FileResponse(path)
