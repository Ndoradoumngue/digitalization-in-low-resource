"""Role/flag permission matrix (GED Phase 1).

Role guards run before any DB access, so a 403 here never needs a
configured mock_db - every request that gets past its guard is answered
by mock_db's default empty result instead, which is fine for asserting
"not 403".
"""

import pytest
from auth import CurrentUser
from conftest import ADMIN, API_KEY_USER, READER, REVIEWER, make_result

DOC = "/api/db/documents/t_default_invoice/00000000-0000-0000-0000-0000000000aa"
PAGE = "/api/ingest/pages/00000000-0000-0000-0000-0000000000bb"
BD = "00000000-0000-0000-0000-0000000000cc"

# (method, path, json) - archive endpoints any signed-in person may call.
BROWSE = [
    ("GET", "/api/db/documents", None),
    ("GET", "/api/db/types", None),
    ("GET", "/api/db/reviewers", None),
    ("GET", "/api/db/series", None),
    ("GET", "/api/db/schema", None),
    ("GET", DOC, None),
    ("GET", f"{DOC}/links", None),
    ("GET", f"{DOC}/access", None),
    ("GET", f"{DOC}/export", None),
    ("GET", "/api/review/count", None),
    ("GET", "/api/review/queue", None),
    ("GET", "/api/documents", None),
    ("GET", f"{DOC}/versions", None),
    ("GET", f"{DOC}/versions/1/file", None),
    ("GET", f"{DOC}/history", None),
    ("GET", "/api/db/series/tree", None),
    ("GET", "/api/db/tags", None),
    ("GET", f"{DOC}/tags", None),
    ("GET", f"{DOC}/link-suggestions", None),
    ("GET", f"{DOC}/download", None),
    ("GET", f"{DOC}/shares", None),
    ("GET", "/api/db/export/jobs", None),
]

# Document actions - admin and reviewer only.
ACT = [
    (
        "PATCH",
        "/api/review/t_default_invoice/00000000-0000-0000-0000-0000000000aa",
        {"action": "reject"},
    ),
    (
        "POST",
        "/api/review/t_default_invoice/00000000-0000-0000-0000-0000000000aa/flag",
        None,
    ),
    (
        "POST",
        "/api/review/t_default_invoice/00000000-0000-0000-0000-0000000000aa/retry",
        None,
    ),
    (
        "POST",
        f"{DOC}/links",
        {"to_table": "t_default_invoice", "to_id": BD, "relation": "concerns"},
    ),
    ("DELETE", "/api/db/links/00000000-0000-0000-0000-0000000000dd", None),
    ("PATCH", f"{DOC}/series", {"series_id": None}),
    ("POST", f"{DOC}/tags", {"name": "urgent"}),
    ("DELETE", f"{DOC}/tags/{BD}", None),
    ("POST", f"{DOC}/versions/1/restore", {}),
    ("POST", f"{PAGE}/retry", None),
    ("POST", f"{PAGE}/reload", None),
    ("POST", f"{PAGE}/skip", None),
    ("POST", f"{PAGE}/cancel", None),
    ("POST", f"/api/ingest/documents/{BD}/pause", None),
    ("POST", f"/api/ingest/pages/resume/{BD}", None),
]

# Ingestion tracking - admin, reviewer and API keys, never a reader.
CONTRIBUTE = [
    ("GET", "/api/ingest/presets", None),
    ("GET", "/api/ingest/batches", None),
    ("GET", f"/api/ingest/status/{BD}", None),
    ("GET", f"/api/ingest/pages/{BD}", None),
]
# The subset answered through mock_db (batches/status use SessionLocal).
CONTRIBUTE_DB = [
    c for c in CONTRIBUTE if "/batches" not in c[1] and "/status/" not in c[1]
]


def _call(client, method, path, body):
    return (
        client.request(method, path, json=body)
        if body is not None
        else client.request(method, path)
    )


@pytest.mark.parametrize("method,path,body", ACT + CONTRIBUTE)
def test_reader_is_read_only(reader_client, mock_db, method, path, body):
    resp = _call(reader_client, method, path, body)
    assert resp.status_code == 403, (method, path, resp.status_code)


@pytest.mark.parametrize("client_name", ["reader_client", "api_key_client"])
def test_new_version_upload_is_for_actors_only(request, client_name, mock_db):
    client = request.getfixturevalue(client_name)
    resp = client.post(f"{DOC}/versions", files={"file": ("v.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "path", ["/api/admin/integrity", "/api/admin/integrity/audit-chain"]
)
def test_integrity_status_is_admin_only(reader_client, mock_db, path):
    assert reader_client.get(path).status_code == 403


def test_reader_cannot_upload(reader_client, mock_db):
    resp = reader_client.post(
        "/api/ingest/upload", files=[("files", ("doc.png", b"x", "image/png"))]
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("method,path,body", BROWSE)
def test_reader_can_browse(reader_client, mock_db, method, path, body):
    resp = _call(reader_client, method, path, body)
    assert resp.status_code != 403, (method, path)


@pytest.mark.parametrize("method,path,body", BROWSE + ACT)
def test_api_key_never_reaches_archive(api_key_client, mock_db, method, path, body):
    resp = _call(api_key_client, method, path, body)
    assert resp.status_code == 403, (method, path, resp.status_code)


@pytest.mark.parametrize("method,path,body", CONTRIBUTE_DB)
def test_api_key_can_follow_ingestion(api_key_client, mock_db, method, path, body):
    resp = _call(api_key_client, method, path, body)
    assert resp.status_code != 403, (method, path)


@pytest.mark.parametrize("method,path,body", ACT + CONTRIBUTE_DB)
def test_reviewer_can_act(auth_client, mock_db, method, path, body):
    resp = _call(auth_client, method, path, body)
    assert resp.status_code != 403, (method, path)


@pytest.mark.parametrize(
    "path",
    ["/api/admin/users", "/api/admin/groups", "/api/admin/audit-log"],
)
def test_reader_never_admin(reader_client, mock_db, path):
    assert reader_client.get(path).status_code == 403


def test_reader_flags_are_ignored():
    """A reader's delegated flags never grant write powers - only can_share."""
    assert READER.can_manage_document_access is False
    assert READER.can_edit_extraction_data is False
    assert READER.can_manage_document_lifecycle is False
    assert READER.can_share_documents is True


def test_reviewer_flags_apply():
    user = CurrentUser(
        **{**REVIEWER.__dict__, "can_manage_lifecycle": True, "can_share": True}
    )
    assert user.can_manage_document_lifecycle is True
    assert user.can_share_documents is True
    assert REVIEWER.can_manage_document_lifecycle is False
    assert REVIEWER.can_share_documents is False


def test_admin_holds_every_flag():
    assert ADMIN.can_manage_document_access
    assert ADMIN.can_edit_extraction_data
    assert ADMIN.can_manage_document_lifecycle
    assert ADMIN.can_share_documents


def test_api_key_holds_no_flag():
    assert not API_KEY_USER.can_share_documents
    assert not API_KEY_USER.can_manage_document_lifecycle


# ── Admin user editor ─────────────────────────────────────────────────────────


def test_update_user_role_and_clearance(admin_client, mock_db):
    mock_db.execute.return_value = make_result(one_or_none=("user-1",))
    resp = admin_client.patch(
        "/api/admin/users/user-1",
        json={"role": "reader", "clearance": "confidential", "can_share": True},
    )
    assert resp.status_code == 200
    sql = str(mock_db.execute.call_args_list[0].args[0])
    params = mock_db.execute.call_args_list[0].args[1]
    assert "role = :role" in sql and "clearance = :clearance" in sql
    # an admin is never demoted through this endpoint
    assert "role <> 'admin'" in sql
    assert params["role"] == "reader" and params["clearance"] == "confidential"


@pytest.mark.parametrize(
    "body",
    [{"role": "admin"}, {"role": "api_key"}, {"clearance": "top-secret"}],
)
def test_update_user_rejects_invalid_values(admin_client, mock_db, body):
    assert admin_client.patch("/api/admin/users/user-1", json=body).status_code == 422


def test_me_returns_flags_and_clearance(reader_client):
    body = reader_client.get("/api/auth/me").json()
    assert body["role"] == "reader"
    assert body["clearance"] == "internal"
    assert body["can_share"] is True
    assert body["can_manage_lifecycle"] is True


# ── _access_where_clause: levels, scopes, clearance ───────────────────────────

import documents_router  # noqa: E402

T = "t_default_invoice"


def _with(user, **changes):
    return CurrentUser(**{**user.__dict__, **changes})


def test_clause_covers_all_three_scopes():
    clause = documents_router._access_where_clause(T, REVIEWER)
    assert "da.scope = 'document'" in clause
    # series grants reach the whole subtree through the materialized path
    assert f'gs.id = "{T}".series_id' in clause
    assert "gs.id = da.series_id OR strpos(gs.path, '/' || da.series_id::text || '/') > 0" in clause
    assert "da.scope = 'doc_type'" in clause
    assert "CAST(:__access_tenant_id AS uuid)" in clause


@pytest.mark.parametrize(
    "permission,satisfying",
    [
        ("read", "('read', 'download', 'edit')"),
        ("download", "('download', 'edit')"),
        ("edit", "('edit')"),
    ],
)
def test_clause_requires_grant_at_or_above_level(permission, satisfying):
    clause = documents_router._access_where_clause(T, REVIEWER, permission)
    assert f"da.permission IN {satisfying}" in clause


def test_reader_never_reaches_edit():
    assert documents_router._access_where_clause(T, READER, "edit") == "FALSE"
    assert documents_router._access_where_clause(T, READER, "download") != "FALSE"


def test_api_key_sees_nothing():
    assert documents_router._access_where_clause(T, API_KEY_USER) == "FALSE"


@pytest.mark.parametrize(
    "clearance,allowed",
    [
        ("public", "('public')"),
        ("internal", "('public', 'internal')"),
        ("confidential", "('public', 'internal', 'confidential')"),
        ("secret", "('public', 'internal', 'confidential', 'secret')"),
        ("bogus", "('public')"),
    ],
)
def test_clause_caps_confidentiality_by_clearance(clearance, allowed):
    clause = documents_router._access_where_clause(
        T, _with(REVIEWER, clearance=clearance)
    )
    assert f"COALESCE(\"{T}\".confidentiality, 'internal') IN {allowed}" in clause


def test_admin_ignores_clearance_and_grants():
    clause = documents_router._access_where_clause(
        T, _with(ADMIN, clearance="public"), "edit"
    )
    assert "confidentiality" not in clause
    assert "sdai_document_access" not in clause


def test_unknown_permission_level_is_a_bug():
    with pytest.raises(ValueError):
        documents_router._access_where_clause(T, REVIEWER, "delete")


def test_access_params_carry_tenant():
    params = documents_router._access_params(REVIEWER)
    assert params["__access_tenant_id"] == REVIEWER.tenant_id


# ── Permission levels on actions ──────────────────────────────────────────────


def _tables(monkeypatch, *routers):
    from unittest.mock import AsyncMock

    for r in routers:
        monkeypatch.setattr(
            r, "_get_tables_columns", AsyncMock(return_value={T: {"id": "uuid"}})
        )


def test_edit_on_read_only_document_is_403(
    extraction_editor_client, mock_db, monkeypatch
):
    _tables(monkeypatch, documents_router)
    mock_db.execute.side_effect = [
        make_result(one_or_none=None),  # edit-level check fails
        make_result(one_or_none=(1,)),  # ...but the document is visible
    ]
    resp = extraction_editor_client.patch(
        f"{DOC.replace('t_default_invoice', T)}/fields", json={"fields": {"a": "b"}}
    )
    assert resp.status_code == 403


def test_export_needs_download(auth_client, mock_db, monkeypatch):
    _tables(monkeypatch, documents_router)
    mock_db.execute.side_effect = [
        make_result(one_or_none=None),  # download-level check fails
        make_result(one_or_none=(1,)),  # read-level passes
    ]
    assert auth_client.get(f"{DOC}/export").status_code == 403
    sql = str(mock_db.execute.call_args_list[0].args[0])
    assert "da.permission IN ('download', 'edit')" in sql


def test_review_queue_lists_only_editable_documents(auth_client, mock_db, monkeypatch):
    import review_router

    _tables(monkeypatch, review_router)
    mock_db.execute.return_value = make_result(rows=[])
    assert auth_client.get("/api/review/queue").status_code == 200
    sql = str(mock_db.execute.call_args_list[0].args[0])
    assert "da.permission IN ('edit')" in sql


def test_reader_review_queue_is_empty_by_construction(
    reader_client, mock_db, monkeypatch
):
    import review_router

    _tables(monkeypatch, review_router)
    mock_db.execute.return_value = make_result(rows=[])
    reader_client.get("/api/review/queue")
    sql = str(mock_db.execute.call_args_list[0].args[0])
    assert "AND FALSE" in sql


# ── Scoped grants (series / doc_type) ─────────────────────────────────────────


def test_scoped_grants_are_admin_only(access_manager_client, mock_db):
    assert access_manager_client.get("/api/db/grants?scope=series").status_code == 403
    body = {
        "scope": "series",
        "series_id": BD,
        "grantee_type": "group",
        "grantee_id": BD,
    }
    assert access_manager_client.post("/api/db/grants", json=body).status_code == 403


def test_create_series_grant(admin_client, mock_db):
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # series exists in tenant
        make_result(one_or_none=(1,)),  # grantee exists in tenant
        make_result(scalar="grant-9"),  # INSERT ... RETURNING
        make_result(),  # log_action
    ]
    body = {
        "scope": "series",
        "series_id": BD,
        "grantee_type": "group",
        "grantee_id": BD,
        "permission": "download",
    }
    resp = admin_client.post("/api/db/grants", json=body)
    assert resp.status_code == 200 and resp.json() == {"id": "grant-9"}
    insert_sql = str(mock_db.execute.call_args_list[2].args[0])
    assert "WHERE scope = 'series' DO UPDATE SET permission" in insert_sql


def test_create_doc_type_grant_checks_tenant_table(admin_client, mock_db, monkeypatch):
    _tables(monkeypatch, documents_router)
    body = {
        "scope": "doc_type",
        "table_name": "t_other_x",
        "grantee_type": "user",
        "grantee_id": BD,
    }
    assert admin_client.post("/api/db/grants", json=body).status_code == 404


def test_scoped_grant_needs_its_target(admin_client, mock_db):
    body = {"scope": "series", "grantee_type": "user", "grantee_id": BD}
    assert admin_client.post("/api/db/grants", json=body).status_code == 422


def test_access_manager_cannot_remove_series_grant(access_manager_client, mock_db):
    mock_db.execute.return_value = make_result(
        one_or_none={
            "id": "g",
            "scope": "series",
            "table_name": None,
            "document_id": None,
            "series_id": BD,
            "grantee_type": "group",
            "grantee_id": BD,
            "permission": "read",
            "granted_by": None,
            "granted_at": None,
        }
    )
    assert access_manager_client.delete("/api/db/access/g").status_code == 403


def test_change_grant_level(access_manager_client, mock_db):
    mock_db.execute.side_effect = [
        make_result(
            one_or_none={
                "id": "g",
                "scope": "document",
                "table_name": T,
                "document_id": BD,
                "series_id": None,
                "grantee_type": "user",
                "grantee_id": BD,
                "permission": "read",
                "granted_by": None,
                "granted_at": None,
            }
        ),
        make_result(one_or_none=(1,)),  # document visible to the manager
        make_result(),  # UPDATE
        make_result(),  # log_action
    ]
    resp = access_manager_client.patch("/api/db/access/g", json={"permission": "edit"})
    assert resp.status_code == 200
    assert mock_db.execute.call_args_list[2].args[1] == {"perm": "edit", "id": "g"}


def test_document_grant_carries_level(access_manager_client, mock_db, monkeypatch):
    _tables(monkeypatch, documents_router)
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # document visible
        make_result(one_or_none=(1,)),  # grantee exists
        make_result(scalar="grant-1"),  # upsert
        make_result(),  # log_action
    ]
    resp = access_manager_client.post(
        f"/api/db/documents/{T}/{BD}/access",
        json={"grantee_type": "user", "grantee_id": BD, "permission": "download"},
    )
    assert resp.status_code == 200
    assert mock_db.execute.call_args_list[2].args[1]["perm"] == "download"


# ── Confidentiality (1.3) ─────────────────────────────────────────────────────


def test_set_confidentiality_needs_access_manager(auth_client, mock_db):
    resp = auth_client.patch(
        f"{DOC}/confidentiality", json={"confidentiality": "public"}
    )
    assert resp.status_code == 403


def test_set_confidentiality_rejects_unknown_level(admin_client, mock_db):
    resp = admin_client.patch(f"{DOC}/confidentiality", json={"confidentiality": "top"})
    assert resp.status_code == 422


def test_manager_cannot_classify_above_own_clearance(access_manager_client, mock_db):
    # ACCESS_MANAGER has the default 'internal' clearance
    resp = access_manager_client.patch(
        f"{DOC}/confidentiality", json={"confidentiality": "secret"}
    )
    assert resp.status_code == 403


def test_set_confidentiality_audits_change(admin_client, mock_db, monkeypatch):
    _tables(monkeypatch, documents_router)
    logged = []

    async def _log(**kw):
        logged.append(kw)

    monkeypatch.setattr(documents_router, "log_action", _log)
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # visible
        make_result(scalar="internal"),  # previous level
        make_result(),  # UPDATE
    ]
    resp = admin_client.patch(
        f"{DOC}/confidentiality", json={"confidentiality": "secret"}
    )
    assert resp.status_code == 200
    assert logged[0]["action"] == "confidentiality_changed"
    assert logged[0]["details"] == {"from": "internal", "to": "secret"}


def test_set_confidentiality_same_level_not_audited(admin_client, mock_db, monkeypatch):
    _tables(monkeypatch, documents_router)
    logged = []

    async def _log(**kw):
        logged.append(kw)

    monkeypatch.setattr(documents_router, "log_action", _log)
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),
        make_result(scalar="secret"),
        make_result(),
    ]
    assert (
        admin_client.patch(
            f"{DOC}/confidentiality", json={"confidentiality": "secret"}
        ).status_code
        == 200
    )
    assert logged == []


def test_set_confidentiality_on_invisible_document_is_404(
    access_manager_client, mock_db, monkeypatch
):
    _tables(monkeypatch, documents_router)
    mock_db.execute.return_value = make_result(one_or_none=None)
    resp = access_manager_client.patch(
        f"{DOC}/confidentiality", json={"confidentiality": "public"}
    )
    assert resp.status_code == 404


# ── Gaps closed (1.4) ─────────────────────────────────────────────────────────

import asyncio  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402


def _image_env(monkeypatch, tmp_path):
    monkeypatch.setattr(documents_router, "_SAFE_FILE_ROOTS", [tmp_path.resolve()])
    f = tmp_path / "scan.png"
    f.write_bytes(b"\x89PNG")
    monkeypatch.setattr(
        documents_router,
        "_get_tables_columns",
        AsyncMock(
            return_value={
                T: {
                    "id": "uuid",
                    "source_image_path": "text",
                    "batch_document_id": "uuid",
                }
            }
        ),
    )
    return f


def test_image_of_invisible_document_is_404(
    reader_client, mock_db, monkeypatch, tmp_path
):
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [
        make_result(rows=[("bd-1",)]),  # the file belongs to upload bd-1 of this tenant
        make_result(
            rows=[(False,)]
        ),  # ...which was filed as a document the reader can't see
    ]
    assert reader_client.get(f"/api/db/image?path={f}").status_code == 404


def test_image_of_visible_document_is_served(
    reader_client, mock_db, monkeypatch, tmp_path
):
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [
        make_result(rows=[("bd-1",)]),
        make_result(rows=[(True,)]),
    ]
    assert reader_client.get(f"/api/db/image?path={f}").status_code == 200


def test_image_with_document_hint_uses_fast_path(
    reader_client, mock_db, monkeypatch, tmp_path
):
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # fast path: readable and references the file
        make_result(one_or_none=(1,)),  # same check, to attribute the download
        make_result(),  # log_action
    ]
    resp = reader_client.get(f"/api/db/image?path={f}&table={T}&id={BD}")
    assert resp.status_code == 200
    sql = str(mock_db.execute.call_args_list[0].args[0])
    assert "id = CAST(:__doc_id AS uuid)" in sql and "sdai_document_access" in sql
    # Not a page image - serving the original is an audited download.
    audit = mock_db.execute.call_args_list[2].args[1]
    assert audit["action"] == "document_downloaded"
    assert (audit["tname"], str(audit["did"])) == (T, BD)


def test_pipeline_image_hidden_from_reader(
    reader_client, mock_db, monkeypatch, tmp_path
):
    """Still in the pipeline (nothing filed): contributors only."""
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [make_result(rows=[("bd-1",)]), make_result(rows=[])]
    assert reader_client.get(f"/api/db/image?path={f}").status_code == 404


def test_pipeline_image_shown_to_reviewer(auth_client, mock_db, monkeypatch, tmp_path):
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [make_result(rows=[("bd-1",)]), make_result(rows=[])]
    assert auth_client.get(f"/api/db/image?path={f}").status_code == 200


def test_unknown_file_is_404(auth_client, mock_db, monkeypatch, tmp_path):
    """A file under a safe root that no upload or document of this tenant
    references (e.g. another tenant's) is never served."""
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [make_result(rows=[]), make_result(rows=[])]
    assert auth_client.get(f"/api/db/image?path={f}").status_code == 404


def test_image_lookup_is_tenant_scoped(auth_client, mock_db, monkeypatch, tmp_path):
    f = _image_env(monkeypatch, tmp_path)
    mock_db.execute.side_effect = [make_result(rows=[]), make_result(rows=[])]
    auth_client.get(f"/api/db/image?path={f}")
    sql = str(mock_db.execute.call_args_list[0].args[0])
    assert sql.count("b.tenant_id = CAST(:tid AS uuid)") == 2


def test_filed_document_permitted_rules(monkeypatch, mock_db):
    monkeypatch.setattr(
        documents_router,
        "_get_tables_columns",
        AsyncMock(return_value={T: {"id": "uuid", "batch_document_id": "uuid"}}),
    )
    run = documents_router._filed_document_permitted
    mock_db.execute.return_value = make_result(rows=[])  # nothing filed yet
    assert asyncio.run(run(["bd"], REVIEWER, "edit")) is True
    mock_db.execute.return_value = make_result(rows=[(False,)])  # filed, not permitted
    assert asyncio.run(run(["bd"], REVIEWER, "edit")) is False
    mock_db.execute.return_value = make_result(rows=[(True,)])
    assert asyncio.run(run(["bd"], REVIEWER, "read")) is True


def test_pages_of_hidden_filed_document_are_404(auth_client, monkeypatch):
    import ingest_router
    from conftest import TENANT_ID, TENANT_SLUG

    monkeypatch.setattr(
        ingest_router,
        "_resolve_tenant_for_batch_document",
        AsyncMock(return_value=(TENANT_ID, TENANT_SLUG)),
    )
    monkeypatch.setattr(
        documents_router, "_filed_document_permitted", AsyncMock(return_value=False)
    )
    assert auth_client.get(f"/api/ingest/pages/{BD}").status_code == 404


def test_page_action_on_hidden_filed_document_is_404(auth_client, monkeypatch):
    import ingest_router
    from conftest import TENANT_ID, TENANT_SLUG

    monkeypatch.setattr(
        ingest_router,
        "_resolve_tenant_for_page",
        AsyncMock(return_value=(TENANT_ID, TENANT_SLUG)),
    )
    monkeypatch.setattr(
        ingest_router, "_batch_document_for_page", AsyncMock(return_value=BD)
    )
    checked = AsyncMock(return_value=False)
    monkeypatch.setattr(documents_router, "_filed_document_permitted", checked)
    monkeypatch.setattr(ingest_router, "_retry_single_page", AsyncMock())
    assert auth_client.post("/api/ingest/pages/p-1/retry").status_code == 404
    assert checked.call_args.args[2] == "edit"


def test_links_to_invisible_documents_are_anonymous(auth_client, mock_db, monkeypatch):
    import datetime as _dt

    _tables(monkeypatch, documents_router)
    link = {
        "id": "l1",
        "from_table": T,
        "from_id": BD,
        "to_table": T,
        "to_id": "00000000-0000-0000-0000-0000000000ee",
        "relation": "concerns",
        "note": "secret deal",
        "created_by": None,
        "created_at": _dt.datetime(2026, 1, 1),
    }
    mock_db.execute.side_effect = [
        make_result(one_or_none=(1,)),  # source document visible
        make_result(rows=[link]),  # links
        make_result(rows=[]),  # the other document is filtered out by access
    ]
    body = auth_client.get(f"/api/db/documents/{T}/{BD}/links").json()
    other = body["links"][0]
    assert other["other_missing"] is True
    assert other["other_id"] == "" and other["other_table"] == ""
    assert other["note"] is None and other["other_display"] is None


def test_audit_log_is_tenant_scoped(admin_client, mock_db):
    mock_db.execute.return_value = make_result(rows=[])
    assert admin_client.get("/api/admin/audit-log").status_code == 200
    sql = str(mock_db.execute.call_args_list[0].args[0])
    params = mock_db.execute.call_args_list[0].args[1]
    assert "starts_with(table_name, :table_prefix)" in sql
    assert params["table_prefix"] == "t_default_"


# Plan de classement / tag vocabulary / filing rules - admins only.
CLASSIFY_ADMIN = [
    ("POST", "/api/db/series", {"name": "x"}),
    ("PATCH", f"/api/db/series/{BD}", {"name": "x"}),
    ("DELETE", f"/api/db/series/{BD}", None),
    ("POST", "/api/db/tags", {"name": "x"}),
    ("PATCH", f"/api/db/tags/{BD}", {"name": "x"}),
    ("POST", f"/api/db/tags/{BD}/merge", {"into_id": BD}),
    ("DELETE", f"/api/db/tags/{BD}", None),
    ("GET", "/api/db/filing-rules", None),
    ("PUT", "/api/db/filing-rules/t_default_invoice", {"series_id": None}),
]


@pytest.mark.parametrize("method,path,body", CLASSIFY_ADMIN)
@pytest.mark.parametrize("client_name", ["auth_client", "reader_client"])
def test_classification_management_is_admin_only(request, mock_db, client_name, method, path, body):
    resp = _call(request.getfixturevalue(client_name), method, path, body)
    assert resp.status_code == 403, (client_name, method, path)

