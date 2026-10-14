"""Full-text search building blocks (GED Phase 4) that need no database:
page-text helpers, snippet segments and the per-table search SQL. The
queries themselves run against PostgreSQL in test_search_pg.py."""

import documents_router
import page_text
from conftest import REVIEWER

COLS = {
    "id": "uuid",
    "source_image_path": "text",
    "batch_document_id": "uuid",
    "page_image_paths": "jsonb",
    "confidence": "text",
    "review_status": "text",
    "ingested_at": "timestamp with time zone",
    "subject": "text",
}


def _select(cols, q="décret"):
    return documents_router._per_table_select(
        "t_default_decree",
        cols,
        q=q,
        confidence=None,
        review_status=None,
        date_from=None,
        date_to=None,
        current_user=REVIEWER,
    )


def test_normalize_collapses_blank_lines_and_drops_nul():
    assert (
        page_text.normalize("  \n\nLigne 1  \n\n\n\nLigne\x00 2\n\n")
        == "Ligne 1\n\nLigne 2"
    )
    assert page_text.normalize("\n \n") == ""


def test_manual_text_flattens_every_typed_value():
    fields = {
        "subject": "Délibération",
        "amount": 1200,
        "approved": True,
        "items": [{"label": "Budget"}, {"label": " "}, None],
        "empty": "",
    }
    assert page_text.manual_text(fields) == "Délibération\n1200\nBudget"
    assert page_text.manual_text({}) is None
    assert page_text.manual_text({"x": ""}) is None


def test_segments_split_highlights_without_markup():
    hl = f"Objet : {documents_router._HL_START}<b>ordonnance</b>{documents_router._HL_STOP} de paiement"
    assert documents_router._segments(hl) == [
        {"text": "Objet : ", "hit": False},
        {"text": "<b>ordonnance</b>", "hit": True},
        {"text": " de paiement", "hit": False},
    ]
    assert documents_router._segments(None) == []
    assert documents_router._segments("rien") == [{"text": "rien", "hit": False}]


def test_search_matches_fields_or_current_pages_inside_the_access_clause():
    sql = _select(COLS)
    assert "websearch_to_tsquery('sdai_fr', :q)" in sql
    assert (
        "to_tsvector('sdai_fr', COALESCE(subject,''))" in sql
    )  # = the GIN index expression
    assert "page_hits" in sql and "page_image_paths" in sql
    assert "AS _field_rank" in sql and "AS _page_rank" in sql
    where = sql.split("\nWHERE ", 1)[1]
    assert "deleted_at IS NULL" in where  # live filter + access clause still apply


def test_table_without_text_fields_is_still_searched_through_its_pages():
    cols = {k: v for k, v in COLS.items() if k != "subject"}
    sql = _select(cols)
    assert sql is not None and "page_hits" in sql
    assert "NULL::real AS _page_rank" not in sql
    # No page link and no text field: nothing to search.
    bare = {"id": "uuid", "source_image_path": "text", "entries": "jsonb"}
    assert _select(bare) is None


def test_listing_without_query_has_no_search_columns():
    sql = _select(COLS, q=None)
    assert "_field_rank" not in sql and "page_hits" not in sql
