"""Original download, share links and selective export - helpers and
guards that need no database (GED Phase 5). The endpoints themselves run
against a real PostgreSQL in test_share_export_pg.py."""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import api_server
import document_files
import export_router
import pytest
import share_router
from auth import get_current_user
from fastapi.routing import APIRoute
from pydantic import ValidationError

# ── Only the public share routes skip get_current_user ───────────────────────

# Sign-in/out, the node-to-node sync API (HMAC-signed by the peer, see
# sync.auth) and the public share-link routes - nothing else, ever.
_UNAUTHENTICATED = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/s/{token}"),
    ("POST", "/s/{token}/download"),
    ("GET", "/api/sync/hello"),
    ("POST", "/api/sync/push"),
    ("GET", "/api/sync/pull"),
    ("GET", "/api/sync/files/info"),
    ("GET", "/api/sync/files"),
    ("PUT", "/api/sync/files"),
}


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_only_known_routes_are_unauthenticated():
    open_routes = {
        (method, route.path)
        for route in api_server.app.routes
        if isinstance(route, APIRoute)
        and get_current_user not in set(_dependency_calls(route.dependant))
        for method in route.methods
    }
    assert open_routes == _UNAUTHENTICATED


# ── File naming ───────────────────────────────────────────────────────────────


def test_download_name_prefixes_record_id_and_strips_paths():
    assert (
        document_files.download_name("INV-2026-000001", "scan.pdf", "x")
        == "INV-2026-000001_scan.pdf"
    )
    assert document_files.download_name(None, "../../etc/passwd", "x") == "passwd"
    assert document_files.download_name(None, 'a"b<c>.pdf', "x") == "a_b_c_.pdf"
    assert document_files.download_name(None, None, "fallback.png") == "fallback.png"
    assert len(document_files.download_name("R", "a" * 500, "x")) == 200


def test_content_disposition_has_ascii_fallback_and_rfc5987():
    header = document_files.content_disposition('Décret n°12 "final".pdf')
    assert header.startswith("attachment; ")
    assert 'filename="Decret n12 _final_.pdf"' in header
    assert "filename*=UTF-8''D%C3%A9cret%20n%C2%B012%20%22final%22.pdf" in header
    assert document_files.content_disposition("a.png", inline=True).startswith(
        "inline; "
    )


def test_pdf_string_escapes_and_encodes():
    assert document_files._pdf_string("a(b)\\") == b"(a\\(b\\)\\\\)"
    assert document_files._pdf_string("é") == b"(\xe9)"  # WinAnsi


def test_watermark_refuses_a_non_pdf():
    with pytest.raises(Exception):
        document_files.watermark_pdf(b"not a pdf", "X", "y")


def test_watermark_only_confidential_pdfs(monkeypatch):
    def doc(**kw):
        base = dict(
            table_name="t",
            document_id="d",
            record_id=None,
            confidentiality="confidential",
            version_no=1,
            current_version=1,
            file_path="/x/a.pdf",
            file_hash=None,
            size=None,
            mime="application/pdf",
            original_filename="a.pdf",
        )
        base.update(kw)
        return document_files.DocumentFile(**base)

    monkeypatch.setattr(document_files, "WATERMARK_CONFIDENTIAL", False)
    assert not doc().needs_watermark()
    monkeypatch.setattr(document_files, "WATERMARK_CONFIDENTIAL", True)
    assert doc().needs_watermark()
    assert doc(confidentiality="secret").needs_watermark()
    assert not doc(confidentiality="internal").needs_watermark()
    assert not doc(mime="image/png", file_path="/x/a.png").needs_watermark()


# ── Share links ───────────────────────────────────────────────────────────────


def _row(**kw):
    base = {
        "revoked_at": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        "max_downloads": None,
        "download_count": 0,
    }
    base.update(kw)
    return base


def test_share_status():
    now = datetime.now(timezone.utc)
    assert share_router._share_status(_row()) == "active"
    assert share_router._share_status(_row(revoked_at=now)) == "revoked"
    assert (
        share_router._share_status(_row(expires_at=now - timedelta(seconds=1)))
        == "expired"
    )
    assert (
        share_router._share_status(_row(max_downloads=2, download_count=2))
        == "exhausted"
    )
    assert (
        share_router._share_status(_row(max_downloads=2, download_count=1)) == "active"
    )


def test_share_level_rules():
    from conftest import ADMIN, REVIEWER

    assert not share_router._may_share_level(ADMIN, "secret")
    assert share_router._may_share_level(ADMIN, "confidential")
    assert not share_router._may_share_level(REVIEWER, "confidential")
    assert share_router._may_share_level(REVIEWER, "internal")


def test_share_body_limits():
    body = share_router.CreateShareBody()
    assert body.expires_in_days == share_router.DEFAULT_EXPIRY_DAYS == 7
    for bad in (
        {"expires_in_days": 0},
        {"expires_in_days": 31},
        {"password": "abc"},
        {"max_downloads": 0},
    ):
        with pytest.raises(ValidationError):
            share_router.CreateShareBody(**bad)


def test_token_shape_and_hash():
    import secrets

    token = secrets.token_urlsafe(32)
    assert share_router._TOKEN_RE.match(token)
    assert not share_router._TOKEN_RE.match(token[:-1])
    assert not share_router._TOKEN_RE.match(token[:-1] + "/")
    assert len(
        share_router._hash_token(token)
    ) == 64 and token not in share_router._hash_token(token)


def test_public_page_escapes_everything():
    page = share_router._landing_page(
        "en",
        "A" * 43,
        _row(password_hash=None, max_downloads=None),
        document_files.DocumentFile(
            table_name="t",
            document_id="d",
            record_id="R-1",
            confidentiality="internal",
            version_no=1,
            current_version=1,
            file_path="/x",
            file_hash=None,
            size=2048,
            mime=None,
            original_filename="<script>alert(1)</script>.pdf",
        ),
    )
    body = page.body.decode()
    # Markup never survives into the name, and the page carries no script.
    assert "<script" not in body and "<h1>R-1_script_.pdf</h1>" in body
    assert page.headers["cache-control"] == "no-store"


def test_malformed_token_is_404_without_touching_the_db(client):
    # No engine in this test - a malformed token must not need one.
    for resp in (client.get("/s/short"), client.post("/s/" + "A" * 44 + "/download")):
        assert resp.status_code == 404 and "not available" in resp.text


# ── Selective export ──────────────────────────────────────────────────────────


def test_export_body_needs_exactly_one_selection():
    with pytest.raises(ValidationError):
        export_router.ExportBody()
    with pytest.raises(ValidationError):
        export_router.ExportBody(
            series_id="00000000-0000-0000-0000-000000000001", search={"q": "x"}
        )
    body = export_router.ExportBody(search={"q": "décret"})
    assert body.versions == "current"
    with pytest.raises(ValidationError):
        export_router.ExportBody(search={}, versions="some")


def test_export_csv_has_bom_and_union_of_fields():
    docs = [
        {
            "table_name": "t1",
            "id": "1",
            "record_id": "R1",
            "series": "A / B",
            "tags": ["x", "y"],
            "files": [{"path": "files/R1_a.pdf"}],
            "fields": {"amount": "10", "items": ["a"]},
        },
        {
            "table_name": "t2",
            "id": "2",
            "tags": [],
            "files": [{"status": "missing"}],
            "fields": {"subject": "Décret"},
        },
    ]
    text = export_router._csv(docs)
    assert text.startswith("﻿")
    lines = text[1:].splitlines()
    assert lines[0].endswith("series,tags,files,amount,items,subject")
    assert (
        "x; y" in lines[1] and "files/R1_a.pdf" in lines[1] and '"[""a""]"' in lines[1]
    )
    assert lines[2].endswith(",,,Décret")


def test_build_zip_manifest_and_warnings(tmp_path, monkeypatch):
    import documents_router

    monkeypatch.setattr(documents_router, "_SAFE_FILE_ROOTS", [tmp_path.resolve()])
    good = tmp_path / "a.pdf"
    good.write_bytes(b"%PDF-1.4 a")
    import hashlib

    good_hash = hashlib.sha256(good.read_bytes()).hexdigest()
    outside = tmp_path.parent / "outside.pdf"
    docs = [
        {"table_name": "t", "id": "d1", "record_id": "R1", "fields": {}, "tags": []},
        {"table_name": "t", "id": "d2", "record_id": None, "fields": {}, "tags": []},
    ]
    versions = [
        {
            "table_name": "t",
            "document_id": "d1",
            "version_no": 1,
            "file_path": str(good),
            "file_hash": good_hash,
            "size": None,
            "mime": "application/pdf",
            "original_filename": "a.pdf",
            "created_at": None,
            "is_current": True,
        },
        {
            "table_name": "t",
            "document_id": "d2",
            "version_no": 1,
            "file_path": str(outside),
            "file_hash": None,
            "size": None,
            "mime": None,
            "original_filename": "o.pdf",
            "created_at": None,
            "is_current": True,
        },
    ]
    ctx = {
        "exported_at": "now",
        "user": "u@x",
        "tenant": "default",
        "node": "local",
        "job_id": "j",
        "mode": "current",
        "documents": 2,
        "files": 2,
    }
    dest = tmp_path / "out" / "j.zip"
    result = export_router._build_zip(dest, docs, versions, ctx)
    assert result["warnings"] == [
        {"document": "d2", "version_no": 1, "problem": "missing"}
    ]
    zf = zipfile.ZipFile(io.BytesIO(dest.read_bytes()))
    manifest = dict(
        reversed(line.split("  ", 1))
        for line in zf.read("manifest.sha256").decode().splitlines()
    )
    assert manifest["files/R1_a.pdf"] == good_hash
    for arc, digest in manifest.items():
        assert hashlib.sha256(zf.read(arc)).hexdigest() == digest
    meta = json.loads(zf.read("metadata.json"))
    assert [d["files"][0]["status"] for d in meta["documents"]] == ["ok", "missing"]
    assert result["zip_sha256"] == hashlib.sha256(dest.read_bytes()).hexdigest()
    # Never over an existing file.
    with pytest.raises(FileExistsError):
        export_router._build_zip(dest, docs, versions, dict(ctx))
