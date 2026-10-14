"""Lifecycle, retention, destruction helpers and route guards (GED Phase 6),
mocked - the real behaviour is in test_lifecycle_pg.py."""

import io
from datetime import date, datetime, timedelta, timezone

import pytest
from conftest import ADMIN, REVIEWER

import destruction
import lifecycle
import pdf_report
import retention
from documents_router import _access_where_clause

DOC = "/api/db/documents/t_default_invoice/00000000-0000-0000-0000-0000000000aa"
RULE = "/api/db/retention-rules/00000000-0000-0000-0000-0000000000dd"
REC = "/api/db/destructions/00000000-0000-0000-0000-0000000000ee"

# Archivist actions - refused (403) to a reviewer without can_manage_lifecycle.
MANAGER_ONLY = [
    ("POST", f"{DOC}/archive", None),
    ("POST", f"{DOC}/trash", None),
    ("POST", f"{DOC}/restore", None),
    ("POST", f"{DOC}/retention/extend", {"until": "2099-01-01"}),
    ("GET", "/api/db/trash", None),
    ("GET", "/api/db/retention-rules", None),
    (
        "POST",
        "/api/db/retention-rules",
        {"name": "x", "scope": "doc_type", "table_name": "t", "duration_months": 1},
    ),
    ("PATCH", RULE, {"name": "y"}),
    ("DELETE", RULE, None),
    ("GET", "/api/db/legal-holds", None),
    (
        "POST",
        "/api/db/legal-holds",
        {"series_id": "00000000-0000-0000-0000-0000000000ff", "reason": "why"},
    ),
    (
        "POST",
        "/api/db/legal-holds/00000000-0000-0000-0000-0000000000ff/release",
        {"reason": "done"},
    ),
    ("GET", "/api/db/destruction/eligible", None),
    ("GET", "/api/db/destructions", None),
    (
        "POST",
        "/api/db/destructions",
        {
            "documents": [
                {"table_name": "t", "id": "00000000-0000-0000-0000-0000000000aa"}
            ]
        },
    ),
    ("POST", f"{REC}/cancel", None),
    ("GET", f"{REC}/pv", None),
    (
        "DELETE",
        "/api/review/t_default_invoice/00000000-0000-0000-0000-0000000000aa",
        None,
    ),
]
ADMIN_ONLY = [
    ("POST", f"{REC}/approve", None),
    ("POST", f"{REC}/reject", None),
    ("POST", "/api/db/retention/run", None),
]


def _call(client, method, path, body):
    return (
        client.request(method, path, json=body)
        if body is not None
        else client.request(method, path)
    )


@pytest.mark.parametrize("method,path,body", MANAGER_ONLY + ADMIN_ONLY)
def test_lifecycle_routes_refuse_plain_reviewers(
    auth_client, mock_db, method, path, body
):
    assert _call(auth_client, method, path, body).status_code == 403


@pytest.mark.parametrize("method,path,body", MANAGER_ONLY + ADMIN_ONLY)
def test_lifecycle_routes_refuse_readers(reader_client, mock_db, method, path, body):
    assert _call(reader_client, method, path, body).status_code == 403


# ── State machine ─────────────────────────────────────────────────────────────


def test_only_listed_transitions_are_allowed():
    allowed = {(f, t): op for (f, t), op in lifecycle.TRANSITIONS.items()}
    for frm in lifecycle.STATUSES:
        for to in lifecycle.STATUSES:
            for op in ("archive", "retention", "extend", "destruction"):
                if allowed.get((frm, to)) == op:
                    lifecycle.check_transition(frm, to, op)
                else:
                    with pytest.raises(lifecycle.LifecycleError):
                        lifecycle.check_transition(frm, to, op)
    # NULL status (rows older than the column default) counts as active.
    lifecycle.check_transition(None, "archived", "archive")
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.check_transition("destroyed", "destroyed", "purge")


def test_read_only_statuses():
    assert lifecycle.can_edit_status(None, False)
    assert lifecycle.can_edit_status("active", False)
    for status in ("archived", "destruction_eligible"):
        assert not lifecycle.can_edit_status(status, False)
        assert lifecycle.can_edit_status(status, True)
    assert not lifecycle.can_edit_status("destroyed", True)


def test_edit_clause_excludes_archived_for_non_managers():
    clause = _access_where_clause("t_default_invoice", REVIEWER, "edit")
    assert lifecycle.editable_sql("t_default_invoice") in clause
    read = _access_where_clause("t_default_invoice", REVIEWER, "read")
    assert lifecycle.editable_sql("t_default_invoice") not in read
    from dataclasses import replace

    manager = replace(REVIEWER, can_manage_lifecycle=True)
    assert lifecycle.editable_sql("t_default_invoice") not in _access_where_clause(
        "t_default_invoice", manager, "edit"
    )
    assert lifecycle.editable_sql("t_default_invoice") not in _access_where_clause(
        "t_default_invoice", ADMIN, "edit"
    )


def test_held_sql_covers_documents_and_series_subtree():
    sql = lifecycle.held_sql("t_default_invoice")
    assert "lhd.table_name = 't_default_invoice'" in sql
    assert "lh.scope = 'series'" in sql and "strpos(hs.path" in sql
    assert "released_at IS NULL" in sql
    with pytest.raises(ValueError):
        lifecycle.held_sql('t"; DROP TABLE x; --')


def test_trash_purge_dates():
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    trashed = now - timedelta(days=lifecycle.TRASH_RETENTION_DAYS)
    assert lifecycle.purge_due(trashed, now)
    assert not lifecycle.purge_due(trashed + timedelta(seconds=1), now)
    assert not lifecycle.purge_due(None, now)
    assert lifecycle.purge_at(trashed) == now


# ── Retention ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2021-03-08", date(2021, 3, 8)),
        ("2021-03-08T10:00:00", date(2021, 3, 8)),
        ("08/03/2021", date(2021, 3, 8)),
        ("8.3.2021", date(2021, 3, 8)),
        ("1er janvier 2020", date(2020, 1, 1)),
        ("15 Février 2019", date(2019, 2, 15)),
        ("31 février 2019", None),
        ("illisible", None),
        ("", None),
        (None, None),
        (date(2020, 5, 1), date(2020, 5, 1)),
    ],
)
def test_parse_date(value, expected):
    assert retention.parse_date(value) == expected


def test_add_months_clamps_to_month_end():
    assert retention.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert retention.add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert retention.add_months(date(2020, 2, 29), 12) == date(2021, 2, 28)
    assert retention.add_months(date(2020, 5, 15), 0) == date(2020, 5, 15)
    assert retention.add_months(date(2020, 11, 30), 3) == date(2021, 2, 28)


def _rule(rid, **kw):
    return {
        "id": rid,
        "trigger_on": "ingested_at",
        "date_field": None,
        "duration_months": 12,
        "final_action": "destroy",
        **kw,
    }


def test_pick_rule_nearest_series_then_type():
    rules = {
        "series": {"root": _rule("r-root"), "child": _rule("r-child")},
        "doc_type": {"t_x_invoice": _rule("r-type")},
    }
    assert (
        retention.pick_rule(rules, "/root/child/leaf/", "t_x_invoice")["id"]
        == "r-child"
    )
    assert retention.pick_rule(rules, "/root/other/", "t_x_invoice")["id"] == "r-root"
    assert retention.pick_rule(rules, "/elsewhere/", "t_x_invoice")["id"] == "r-type"
    assert retention.pick_rule(rules, None, "t_x_invoice")["id"] == "r-type"
    assert retention.pick_rule(rules, None, "t_x_letter") is None


def test_compute_until():
    ingested = datetime(2020, 1, 15, 9, 0, tzinfo=timezone.utc)
    row = {
        "ingested_at": ingested,
        "reviewed_at": None,
        "archived_at": None,
        "__date_date_acte": "01/06/2019",
    }
    assert retention.compute_until(_rule("r"), row) == date(2021, 1, 15)
    assert retention.compute_until(_rule("r", final_action="keep_forever"), row) is None
    assert retention.compute_until(_rule("r", trigger_on="archived_at"), row) is None
    assert retention.compute_until(
        _rule(
            "r", trigger_on="document_date", date_field="date_acte", duration_months=120
        ),
        row,
    ) == date(2029, 6, 1)
    assert retention.compute_until(None, row) is None


# ── Destruction report ────────────────────────────────────────────────────────


def _record(**kw):
    return {
        "id": "24788b67-ad8e-4c3c-be58-2b4cbfb9a69a",
        "kind": "retention",
        "proposed_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
        "decided_at": datetime(2026, 10, 15, tzinfo=timezone.utc),
        "executed_at": datetime(2026, 10, 15, tzinfo=timezone.utc),
        "proposed_by_email": "archiviste@example.org",
        "decided_by_email": "admin@example.org",
        "comment": "DUA échue",
        "decision_note": None,
        "document_count": 1,
        "documents": [
            {
                "table_name": "t_default_invoice",
                "id": "d1",
                "record_id": "INV-2026-000001",
                "document_type": "facture",
                "ingested_at": "2020-01-01T00:00:00+00:00",
                "original_hash": "a" * 64,
                "versions": [
                    {"version_no": 1, "file_hash": "a" * 64},
                    {"version_no": 2, "file_hash": "b" * 64},
                ],
            }
        ],
        **kw,
    }


def test_pv_is_a_readable_pdf_in_french_then_english():
    from pypdf import PdfReader

    data = destruction.render_pv(_record(), "Ministère de l'Intérieur", ["fr", "en"], 3)
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    assert "Procès-verbal d'élimination ELIM-2026-24788B67" in text
    assert "Destruction certificate ELIM-2026-24788B67" in text
    assert text.index("Procès-verbal") < text.index("Destruction certificate")
    assert "Ministère de l'Intérieur" in text and "INV-2026-000001" in text
    assert "restic" in text
    # The original that is also version 1 is listed once.
    assert text.count("original, v1") == 2  # once per language part


def test_pv_for_trash_purge_names_the_system():
    from pypdf import PdfReader

    record = _record(kind="trash_purge", proposed_by_email=None, decided_by_email=None)
    text = (
        PdfReader(io.BytesIO(destruction.render_pv(record, "T", ["fr"], 0)))
        .pages[0]
        .extract_text()
    )
    assert "Purge de la corbeille" in text and "Système (automatique)" in text


def test_report_wraps_long_words_and_paginates():
    report = pdf_report.Report(footer="x")
    report.table(["h"], [["x" * 400]] * 80, widths=[1.0])
    data = report.build()
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(data)).pages) > 1
    for line in pdf_report.wrap("y" * 400, 100, 9):
        assert pdf_report.text_width(line, 9) <= 100


def test_record_number():
    assert (
        destruction.record_number("abcdef12-0000", datetime(2027, 1, 1))
        == "ELIM-2027-ABCDEF12"
    )
