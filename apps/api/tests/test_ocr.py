"""Tests for OCR endpoints: /api/documents and /images/raw."""

import json
import pytest
from conftest import make_ocr_doc


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_list_documents_requires_auth(client):
    assert client.get("/api/documents").status_code == 401


def test_get_document_requires_auth(client):
    assert client.get("/api/documents/doc1.png").status_code == 401


def test_get_all_results_requires_auth(client):
    assert client.get("/api/results").status_code == 401


def test_raw_image_requires_auth(client):
    assert client.get("/images/raw/doc1.png").status_code == 401


def test_preprocessed_image_requires_auth(client):
    assert client.get("/images/preprocessed/doc1.png").status_code == 401


# ── Document list ─────────────────────────────────────────────────────────────

def test_list_documents_empty_dir(auth_client):
    resp = auth_client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_documents_returns_all_images(auth_client, dirs):
    (dirs["docs"] / "a.png").write_bytes(b"fake")
    (dirs["docs"] / "b.jpg").write_bytes(b"fake")
    (dirs["docs"] / "not_an_image.txt").write_text("ignored")

    resp = auth_client.get("/api/documents")
    assert resp.status_code == 200
    data = resp.json()
    filenames = [d["filename"] for d in data]
    assert sorted(filenames) == ["a.png", "b.jpg"]


def test_list_documents_has_result_flag(auth_client, dirs):
    (dirs["docs"] / "doc1.png").write_bytes(b"fake")
    (dirs["docs"] / "doc2.png").write_bytes(b"fake")
    (dirs["json"] / "doc1_results.json").write_text(json.dumps(make_ocr_doc("doc1.png")))

    resp = auth_client.get("/api/documents")
    data = {d["filename"]: d["hasResult"] for d in resp.json()}
    assert data["doc1.png"] is True
    assert data["doc2.png"] is False


# ── Per-document result ───────────────────────────────────────────────────────

def test_get_document_result_success(auth_client, dirs):
    (dirs["docs"] / "doc1.png").write_bytes(b"fake")
    (dirs["json"] / "doc1_results.json").write_text(json.dumps(make_ocr_doc("doc1.png")))

    resp = auth_client.get("/api/documents/doc1.png")
    assert resp.status_code == 200
    assert resp.json()["filename"] == "doc1.png"


def test_get_document_result_not_found(auth_client, dirs):
    (dirs["docs"] / "doc1.png").write_bytes(b"fake")
    resp = auth_client.get("/api/documents/doc1.png")
    assert resp.status_code == 404


# ── Combined results file ─────────────────────────────────────────────────────

def test_get_all_results_success(auth_client, dirs):
    payload = {"doc1.png": make_ocr_doc("doc1.png")}
    (dirs["json"] / "_all_results.json").write_text(json.dumps(payload))

    resp = auth_client.get("/api/results")
    assert resp.status_code == 200
    assert "doc1.png" in resp.json()


def test_get_all_results_missing(auth_client):
    resp = auth_client.get("/api/results")
    assert resp.status_code == 404


# ── Image serving ─────────────────────────────────────────────────────────────

def test_serve_raw_image(auth_client, dirs):
    (dirs["docs"] / "doc1.png").write_bytes(b"fake-png-bytes")
    resp = auth_client.get("/images/raw/doc1.png")
    assert resp.status_code == 200
    assert resp.content == b"fake-png-bytes"


def test_serve_raw_image_not_found(auth_client):
    resp = auth_client.get("/images/raw/missing.png")
    assert resp.status_code == 404


def test_serve_preprocessed_image(auth_client, dirs):
    (dirs["preprocessed"] / "doc1_preprocessed.png").write_bytes(b"preprocessed-bytes")
    resp = auth_client.get("/images/preprocessed/doc1.png")
    assert resp.status_code == 200
    assert resp.content == b"preprocessed-bytes"


def test_serve_preprocessed_image_not_found(auth_client):
    resp = auth_client.get("/images/preprocessed/missing.png")
    assert resp.status_code == 404
