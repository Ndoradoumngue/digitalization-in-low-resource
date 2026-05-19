"""Tests for VLM endpoints: /api/vlm/*."""

import json
import pytest
from conftest import make_vlm_entry


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_list_vlm_requires_auth(client):
    assert client.get("/api/vlm/documents").status_code == 401


def test_get_vlm_result_requires_auth(client):
    assert client.get("/api/vlm/documents/doc1.png").status_code == 401


def test_get_all_vlm_requires_auth(client):
    assert client.get("/api/vlm/results").status_code == 401


# ── Document list ─────────────────────────────────────────────────────────────

def test_list_vlm_no_results_file(auth_client):
    """Returns 404 when vlm_qwen25_results.json has not been generated yet."""
    resp = auth_client.get("/api/vlm/documents")
    assert resp.status_code == 404


def test_list_vlm_documents_has_result_flag(auth_client, dirs):
    (dirs["docs"] / "doc1.png").write_bytes(b"fake")
    (dirs["docs"] / "doc2.png").write_bytes(b"fake")
    payload = {"doc1.png": make_vlm_entry("doc1.png")}
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/vlm/documents")
    assert resp.status_code == 200
    data = {d["filename"]: d["hasResult"] for d in resp.json()}
    assert data["doc1.png"] is True
    assert data["doc2.png"] is False


def test_list_vlm_documents_sorted(auth_client, dirs):
    for name in ["c.png", "a.png", "b.jpg"]:
        (dirs["docs"] / name).write_bytes(b"fake")
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps({}))

    resp = auth_client.get("/api/vlm/documents")
    filenames = [d["filename"] for d in resp.json()]
    assert filenames == sorted(filenames)


# ── Per-document VLM result ───────────────────────────────────────────────────

def test_get_vlm_result_success(auth_client, dirs):
    payload = {"doc1.png": make_vlm_entry("doc1.png", confidence="high")}
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/vlm/documents/doc1.png")
    assert resp.status_code == 200
    data = resp.json()
    assert data["_filename"] == "doc1.png"
    assert data["extraction_confidence"] == "high"


def test_get_vlm_result_medium(auth_client, dirs):
    payload = {"doc1.png": make_vlm_entry("doc1.png", confidence="medium")}
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/vlm/documents/doc1.png")
    assert resp.status_code == 200
    assert resp.json()["extraction_confidence"] == "medium"


def test_get_vlm_result_not_found(auth_client, dirs):
    payload = {"doc1.png": make_vlm_entry("doc1.png")}
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/vlm/documents/missing.png")
    assert resp.status_code == 404


# ── All VLM results ───────────────────────────────────────────────────────────

def test_get_all_vlm_results(auth_client, dirs):
    payload = {
        "doc1.png": make_vlm_entry("doc1.png", "high"),
        "doc2.png": make_vlm_entry("doc2.png", "low"),
    }
    (dirs["json"] / "vlm_qwen25_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/vlm/results")
    assert resp.status_code == 200
    data = resp.json()
    assert "doc1.png" in data
    assert "doc2.png" in data


def test_get_all_vlm_results_missing_file(auth_client):
    resp = auth_client.get("/api/vlm/results")
    assert resp.status_code == 404
