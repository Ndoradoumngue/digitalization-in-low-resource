"""GED Phase 2 unit tests that need no database - versioning helpers, the
re-extraction routing in Stage 3, the version fixity pass and the field
diff. The database-backed behaviour (routes, history, audit chain) is in
test_versions_pg.py."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import ingest_router
import pytest
from conftest import TENANT_SLUG, make_result
from history import field_changes, json_value
from versions_router import _safe_filename

# ── history.field_changes ────────────────────────────────────────────────────


def test_field_changes_only_reports_real_changes():
    old = {"a": "x", "b": "same", "c": None, "d": '["p", "q"]', "e": ""}
    new = {"a": "y", "b": "same", "c": "", "d": ["p", "q"], "e": None}
    # "" vs None and a JSON string vs the same list are not changes.
    assert field_changes(old, new, ["a", "b", "c", "d", "e"]) == {
        "a": {"old": "x", "new": "y"}
    }


def test_field_changes_ignores_fields_not_asked_for():
    assert field_changes({"a": 1, "b": 1}, {"a": 2, "b": 2}, ["a"]) == {
        "a": {"old": 1, "new": 2}
    }


def test_json_value_keeps_structure_and_stringifies_the_rest():
    import datetime
    import uuid

    u = uuid.uuid4()
    assert json_value([1, {"a": None}]) == [1, {"a": None}]
    assert json_value(datetime.date(2026, 10, 9)) == "2026-10-09"
    assert json_value(u) == str(u)


def test_record_field_changes_rejects_unknown_source():
    from history import record_field_changes

    with pytest.raises(ValueError):
        asyncio.run(
            record_field_changes(
                None,
                tenant_id="t",
                table_name="x",
                document_id="d",
                changes={},
                changed_by=None,
                source="magic",
                version_no=1,
            )
        )


# ── versions_router._safe_filename ───────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("scan v2.PNG", "scan_v2.png"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("ordre de mission – n°12.pdf", "ordre_de_mission_n_12.pdf"),
        ("", "file"),
        ("." * 5, "file"),
    ],
)
def test_safe_filename(name, expected):
    assert _safe_filename(name) == expected


# ── Stage 3 routing for a version re-extraction ──────────────────────────────


def _vlm_result(*, error=None, fields=None):
    return ingest_router._VlmResult(
        doc_id="bd-1",
        batch_id="b-1",
        dest_paths=[Path("/img/p1.png"), Path("/img/p2.png")],
        pdf_path=Path("/up/v2.pdf"),
        filename="v2.pdf",
        t0=0.0,
        fields=fields
        if fields is not None
        else {"document_type": "x", "extraction_confidence": "high", "a": "1"},
        error=error,
        content_hash="h",
        tenant_id="tid",
        tenant_slug=TENANT_SLUG,
    )


@pytest.fixture()
def stage3(monkeypatch):
    target = ingest_router._ReextractTarget("t_default_x", "doc-1", 2)
    mocks = {
        "_batch_document_exists": AsyncMock(return_value=True),
        "_reextract_target": AsyncMock(return_value=target),
        "_apply_reextraction": AsyncMock(
            return_value={"fields_changed": ["a"], "detected_type": "x"}
        ),
        "_update_doc_status": AsyncMock(),
        "_document_has_flagged_pages": AsyncMock(return_value=False),
        "_store_extraction": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(ingest_router, name, mock)
    mocks["target"] = target
    return mocks


def test_stage3_reextracts_into_the_target_instead_of_filing(stage3):
    asyncio.run(ingest_router._stage3_process(_vlm_result()))
    stage3["_store_extraction"].assert_not_awaited()
    args, kwargs = stage3["_apply_reextraction"].await_args
    assert args[:2] == ("bd-1", stage3["target"])
    assert kwargs["review_status"] == "auto_approved"
    assert kwargs["page_image_paths"] == ["/img/p1.png", "/img/p2.png"]
    assert kwargs["source_pdf_path"] == "/up/v2.pdf"
    assert stage3["_update_doc_status"].await_args.args[1] == "completed"


def test_stage3_reextraction_needs_review_when_pages_flagged(stage3):
    stage3["_document_has_flagged_pages"].return_value = True
    asyncio.run(ingest_router._stage3_process(_vlm_result()))
    assert (
        stage3["_apply_reextraction"].await_args.kwargs["review_status"]
        == "review_required"
    )
    assert stage3["_update_doc_status"].await_args.args[1] == "review_required"


def test_stage3_failed_reextraction_leaves_the_document_untouched(stage3):
    asyncio.run(
        ingest_router._stage3_process(
            _vlm_result(error="every page timed out", fields={})
        )
    )
    stage3["_apply_reextraction"].assert_not_awaited()
    stage3["_store_extraction"].assert_not_awaited()
    assert stage3["_update_doc_status"].await_args.args[1] == "crashed"


def test_stage3_reextraction_of_a_vanished_document_is_crashed(stage3):
    stage3["_apply_reextraction"].return_value = None
    asyncio.run(ingest_router._stage3_process(_vlm_result()))
    assert stage3["_update_doc_status"].await_args.args[1] == "crashed"


def test_stage3_without_target_files_a_new_document(stage3):
    stage3["_reextract_target"].return_value = None
    asyncio.run(ingest_router._stage3_process(_vlm_result()))
    stage3["_store_extraction"].assert_awaited_once()
    stage3["_apply_reextraction"].assert_not_awaited()


# ── Fixity over version files ────────────────────────────────────────────────


def test_version_fixity_checks_each_file_once(mock_db, tmp_path):
    good = tmp_path / "v1.pdf"
    good.write_bytes(b"%PDF good")
    good_hash = hashlib.sha256(good.read_bytes()).hexdigest()
    bad = tmp_path / "v2.png"
    bad.write_bytes(b"changed")
    gone = tmp_path / "v3.png"
    mock_db.execute.side_effect = [
        make_result(
            rows=[
                ("t_default_x", "doc-1", 1, str(good), good_hash),
                ("t_default_x", "doc-1", 2, str(bad), "0" * 64),
                (
                    "t_default_x",
                    "doc-1",
                    3,
                    str(good),
                    good_hash,
                ),  # restore of v1 - same file
                ("t_default_x", "doc-1", 4, str(gone), None),
                ("t_default_x", "doc-2", 1, str(tmp_path / "legacy.png"), None),
            ]
        ),
        make_result(),  # log_action - v2 mismatch
        make_result(),  # log_action - v4 missing
        make_result(),  # log_action - doc-2 v1 missing
    ]
    counts = {"checked": 0, "ok": 0, "mismatched": 0, "missing": 0}
    asyncio.run(ingest_router._check_version_files(TENANT_SLUG, counts))
    assert counts == {
        "checked": 0,
        "ok": 0,
        "mismatched": 1,
        "missing": 2,
        "versions_checked": 5,
        "versions_ok": 2,
    }
    logged = [c.args[1] for c in mock_db.execute.call_args_list[1:]]
    assert [entry["action"] for entry in logged] == ["integrity_check_failed"] * 3
    assert '"version_no": 2' in logged[0]["details"]


def test_reextracted_pdf_is_not_compared_with_the_first_upload_hash(
    mock_db, tmp_path, monkeypatch
):
    """After a version re-extraction source_pdf_path is the version's file -
    it has its own hash in the version pass, never content_hash."""
    monkeypatch.setattr(ingest_router, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(ingest_router, "_check_version_files", AsyncMock())
    pdf = tmp_path / "versions" / "default" / "doc-1" / "v2_new.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF new")
    img = tmp_path / "p1.png"
    img.write_bytes(b"png")
    mock_db.execute.side_effect = [
        make_result(rows=[("t_default_x",)]),
        make_result(
            rows=[("doc-1", str(img), str(pdf), "hash-of-the-first-upload", None, None)]
        ),
    ]
    counts = asyncio.run(ingest_router._run_integrity_check(TENANT_SLUG))
    assert counts == {"checked": 1, "ok": 1, "mismatched": 0, "missing": 0}
