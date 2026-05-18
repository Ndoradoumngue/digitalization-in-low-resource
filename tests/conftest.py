"""Shared pytest fixtures for the SDAI API test suite."""

import json
import pytest
from fastapi.testclient import TestClient

import api_server

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """Create a temporary document tree and patch all module-level paths."""
    docs_dir      = tmp_path / "anonymized_docs"
    json_dir      = tmp_path / "ocr_results" / "json"
    preprocess_dir = tmp_path / "ocr_results" / "preprocessed"
    docs_dir.mkdir(parents=True)
    json_dir.mkdir(parents=True)
    preprocess_dir.mkdir(parents=True)

    monkeypatch.setattr(api_server, "DOCS_DIR",          docs_dir)
    monkeypatch.setattr(api_server, "JSON_DIR",          json_dir)
    monkeypatch.setattr(api_server, "PREPROCESS_DIR",    preprocess_dir)
    monkeypatch.setattr(api_server, "VLM_RESULTS_FILE",  json_dir / "vlm_qwen25_results.json")

    return {"docs": docs_dir, "json": json_dir, "preprocessed": preprocess_dir}


@pytest.fixture()
def client(dirs):
    """Unauthenticated TestClient with patched filesystem paths."""
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """TestClient already logged in as the default admin user."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "changeme"},
    )
    assert resp.status_code == 200, f"Login failed in fixture: {resp.text}"
    return client


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_ENGINE_RESULT = {
    "text": "texte extrait",
    "confidence": 0.92,
    "time": 1.23,
    "error": None,
}

VALID_VARIANT = {
    "tesseract": VALID_ENGINE_RESULT,
    "easyocr":   VALID_ENGINE_RESULT,
    "surya":     VALID_ENGINE_RESULT,
}

def make_ocr_doc(filename: str = "doc1.png") -> dict:
    return {"filename": filename, "raw": VALID_VARIANT, "preprocessed": VALID_VARIANT}


def make_vlm_entry(
    filename: str = "doc1.png",
    confidence: str = "high",
) -> dict:
    return {
        "_filename": filename,
        "_time": 2.5,
        "extraction_confidence": confidence,
        "document_type": "ordre_de_mission",
        "quality_issues": [],
        "reference_number": None,
        "date": None,
        "person_names": None,
        "functions": None,
        "destination_or_subject": None,
        "organisation": None,
        "signatory": None,
        "budget_line": None,
        "language": "FR",
        "bilingual_layout": None,
    }
