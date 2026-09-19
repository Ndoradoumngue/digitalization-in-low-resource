"""Shared pytest fixtures for the SDAI API test suite."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

import api_server
from auth import CurrentUser, get_current_user, require_admin

# ── Constants ─────────────────────────────────────────────────────────────────

TENANT_ID   = "00000000-0000-0000-0000-000000000099"
TENANT_SLUG = "default"
TENANT_NAME = "Default"

REVIEWER = CurrentUser(
    id="00000000-0000-0000-0000-000000000001",
    email="reviewer@example.com",
    full_name="Test Reviewer",
    role="reviewer",
    tenant_id=TENANT_ID,
    tenant_slug=TENANT_SLUG,
    tenant_name=TENANT_NAME,
    can_manage_access=False,
    can_edit_extraction=False,
    group_ids=[],
    locale="en",
)

ADMIN = CurrentUser(
    id="00000000-0000-0000-0000-000000000002",
    email="admin@example.com",
    full_name="Test Admin",
    role="admin",
    tenant_id=TENANT_ID,
    tenant_slug=TENANT_SLUG,
    tenant_name=TENANT_NAME,
    can_manage_access=False,
    can_edit_extraction=False,
    group_ids=[],
    locale="en",
)

# A reviewer delegated the can_manage_access permission — for testing the
# "admin OR can_manage_access" tier separately from plain reviewer/admin.
ACCESS_MANAGER = CurrentUser(
    id="00000000-0000-0000-0000-000000000003",
    email="access-manager@example.com",
    full_name="Test Access Manager",
    role="reviewer",
    tenant_id=TENANT_ID,
    tenant_slug=TENANT_SLUG,
    tenant_name=TENANT_NAME,
    can_manage_access=True,
    can_edit_extraction=False,
    group_ids=[],
    locale="en",
)

# A reviewer delegated the can_edit_extraction permission — for testing the
# "admin OR can_edit_extraction" tier separately from plain reviewer/admin.
EXTRACTION_EDITOR = CurrentUser(
    id="00000000-0000-0000-0000-000000000004",
    email="extraction-editor@example.com",
    full_name="Test Extraction Editor",
    role="reviewer",
    tenant_id=TENANT_ID,
    tenant_slug=TENANT_SLUG,
    tenant_name=TENANT_NAME,
    can_manage_access=False,
    can_edit_extraction=True,
    group_ids=[],
    locale="en",
)


# ── DB mock helper ────────────────────────────────────────────────────────────

def make_result(rows=None, *, scalar=None, one_or_none=None, one=None, rowcount=0):
    """
    Build a mock CursorResult that covers the SQLAlchemy async access patterns
    used throughout the routers:

      .mappings().all()           — list queries (list_documents, review_queue)
      .mappings().one_or_none()   — single-row fetch (get_document_detail, patch_review)
      for row in result           — column/FK discovery (_get_tables_columns, get_schema)
      .scalar()                   — aggregate / INSERT RETURNING scalar
      .one_or_none()              — user lookup in auth.get_current_user
      .one()                      — stats tuple in list_types / get_schema
      .rowcount                   — DML row count (flag_review)
      .fetchall()                 — batch status query
    """
    rows = rows if rows is not None else []
    result = MagicMock()

    mappings = MagicMock()
    mappings.all.return_value         = rows
    mappings.one_or_none.return_value = one_or_none
    result.mappings.return_value      = mappings

    result.__iter__       = MagicMock(return_value=iter(rows))
    result.scalar.return_value        = scalar
    result.one_or_none.return_value   = one_or_none
    result.one.return_value           = one
    result.rowcount                   = rowcount
    result.fetchall.return_value      = rows

    return result


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    """Create a temporary document tree, patch filesystem paths, and stub DB/cache init."""
    docs_dir       = tmp_path / "anonymized_docs"
    json_dir       = tmp_path / "ocr_results" / "json"
    preprocess_dir = tmp_path / "ocr_results" / "preprocessed"
    docs_dir.mkdir(parents=True)
    json_dir.mkdir(parents=True)
    preprocess_dir.mkdir(parents=True)

    monkeypatch.setattr(api_server, "DOCS_DIR",         docs_dir)
    monkeypatch.setattr(api_server, "JSON_DIR",         json_dir)
    monkeypatch.setattr(api_server, "PREPROCESS_DIR",   preprocess_dir)
    monkeypatch.setattr(api_server, "VLM_RESULTS_FILE", json_dir / "vlm_qwen25_results.json")

    import ingest_router
    import cache as cache_module

    async def _noop() -> None:
        pass

    monkeypatch.setattr(ingest_router,  "startup",    _noop)
    monkeypatch.setattr(ingest_router,  "shutdown",   _noop)
    monkeypatch.setattr(cache_module,   "init_cache", _noop)

    # InMemoryBackend._store is a class-level dict shared across all instances.
    # Clear it explicitly before each test so cached responses from one test
    # never bleed into the next one.
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.inmemory import InMemoryBackend
    InMemoryBackend._store.clear()
    FastAPICache.init(InMemoryBackend(), prefix="sdai-test")

    return {"docs": docs_dir, "json": json_dir, "preprocessed": preprocess_dir}


@pytest.fixture()
def client(dirs):
    """Unauthenticated TestClient. No dependency overrides — tests the real auth guards."""
    api_server.app.dependency_overrides.clear()
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(dirs):
    """TestClient authenticated as a reviewer via dependency override."""
    api_server.app.dependency_overrides[get_current_user] = lambda: REVIEWER
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def admin_client(dirs):
    """TestClient authenticated as an admin via dependency override."""
    api_server.app.dependency_overrides[get_current_user] = lambda: ADMIN
    api_server.app.dependency_overrides[require_admin]    = lambda: ADMIN
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def access_manager_client(dirs):
    """TestClient authenticated as a reviewer with can_manage_access=True.
    Only get_current_user is overridden — require_access_manager's real
    logic still runs and evaluates can_manage_document_access itself,
    the same way require_admin's real 403 check runs for auth_client."""
    api_server.app.dependency_overrides[get_current_user] = lambda: ACCESS_MANAGER
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def extraction_editor_client(dirs):
    """TestClient authenticated as a reviewer with can_edit_extraction=True.
    Only get_current_user is overridden — require_extraction_editor's real
    logic still runs and evaluates can_edit_extraction_data itself."""
    api_server.app.dependency_overrides[get_current_user] = lambda: EXTRACTION_EDITOR
    with TestClient(api_server.app, raise_server_exceptions=True) as c:
        yield c
    api_server.app.dependency_overrides.clear()


@pytest.fixture()
def mock_db(monkeypatch):
    """
    Replace ingest_router.engine with a fake async engine.

    Returns the shared ``conn`` MagicMock so individual tests can configure:
        mock_db.execute.return_value = make_result(...)
        mock_db.execute.side_effect  = [make_result(...), ...]

    Both engine.connect() and engine.begin() yield the same conn so that
    log_action() (which uses .begin()) and endpoint code (which uses .connect())
    share one mock without extra setup.
    """
    import ingest_router

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=make_result())
    conn.commit  = AsyncMock()

    connect_ctx = MagicMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=conn)
    connect_ctx.__aexit__  = AsyncMock(return_value=False)

    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=conn)
    begin_ctx.__aexit__  = AsyncMock(return_value=False)

    fake_engine = MagicMock()
    fake_engine.connect.return_value = connect_ctx
    fake_engine.begin.return_value   = begin_ctx

    monkeypatch.setattr(ingest_router, "engine", fake_engine)
    return conn


# ── Helpers for OCR/VLM tests (backward-compatible) ──────────────────────────

VALID_ENGINE_RESULT = {
    "text":       "texte extrait",
    "confidence": 0.92,
    "time":       1.23,
    "error":      None,
}

VALID_VARIANT = {
    "tesseract": VALID_ENGINE_RESULT,
    "easyocr":   VALID_ENGINE_RESULT,
    "surya":     VALID_ENGINE_RESULT,
}


def make_ocr_doc(filename: str = "doc1.png") -> dict:
    return {"filename": filename, "raw": VALID_VARIANT, "preprocessed": VALID_VARIANT}


def make_vlm_entry(filename: str = "doc1.png", confidence: str = "high") -> dict:
    return {
        "_filename":              filename,
        "_time":                  2.5,
        "extraction_confidence":  confidence,
        "document_type":          "ordre_de_mission",
        "quality_issues":         [],
        "reference_number":       None,
        "date":                   None,
        "person_names":           None,
        "functions":              None,
        "destination_or_subject": None,
        "organisation":           None,
        "signatory":              None,
        "budget_line":            None,
        "language":               "FR",
        "bilingual_layout":       None,
    }
