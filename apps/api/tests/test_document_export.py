"""Per-document export (GET /api/db/documents/{table}/{id}/export) and the
shared export serializers."""

import json
from unittest.mock import AsyncMock

import pytest

import documents_router
from conftest import make_result
from export_format import insert_statement, list_to_csv

TABLE_COLS = {"my_table": {"id": "uuid", "title": "text", "entries": "jsonb"}}
ROW = {
    "id": "0f7e8a52-1111-2222-3333-444455556666",
    "title": "Lexique l'Abbé",
    "entries": [{"kabalay": "bàdú", "french": "N. chat"}, {"kabalay": "gùmā", "french": "rat, \"souris\""}],
}


@pytest.fixture
def doc(mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns", AsyncMock(return_value=TABLE_COLS))
    mock_db.execute.return_value = make_result(one_or_none=ROW)
    return mock_db


def test_export_requires_auth(client):
    assert client.get("/api/db/documents/my_table/x/export").status_code == 401


def test_export_json(auth_client, doc):
    resp = auth_client.get(f"/api/db/documents/my_table/{ROW['id']}/export?format=json")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="my_table_0f7e8a52.json"'
    body = resp.json()
    assert body["table"] == "my_table"
    assert body["document"]["entries"][0] == {"kabalay": "bàdú", "french": "N. chat"}


def test_export_sql(auth_client, doc):
    resp = auth_client.get(f"/api/db/documents/my_table/{ROW['id']}/export?format=sql")
    assert resp.status_code == 200
    sql = resp.text
    assert 'CREATE TABLE IF NOT EXISTS "my_table" ("id" uuid, "title" text, "entries" jsonb);' in sql
    assert "'Lexique l''Abbé'" in sql  # quotes escaped
    assert "::jsonb" in sql


def test_export_csv_of_a_list_field(auth_client, doc):
    resp = auth_client.get(f"/api/db/documents/my_table/{ROW['id']}/export?format=csv&field=entries")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].endswith('my_table_0f7e8a52_entries.csv"')
    lines = resp.content.decode("utf-8-sig").splitlines()
    assert lines == ["kabalay,french", "bàdú,N. chat", 'gùmā,"rat, ""souris"""']


@pytest.mark.parametrize("query", ["format=csv", "format=csv&field=title", "format=csv&field=missing"])
def test_export_csv_needs_a_list_field(auth_client, doc, query):
    resp = auth_client.get(f"/api/db/documents/my_table/{ROW['id']}/export?{query}")
    assert resp.status_code == 422


def test_export_rejects_unknown_format(auth_client, doc):
    assert auth_client.get(f"/api/db/documents/my_table/{ROW['id']}/export?format=xml").status_code == 422


def test_export_document_not_visible(auth_client, mock_db, monkeypatch):
    monkeypatch.setattr(documents_router, "_get_tables_columns", AsyncMock(return_value=TABLE_COLS))
    mock_db.execute.return_value = make_result(one_or_none=None)
    assert auth_client.get("/api/db/documents/my_table/x/export").status_code == 404


def test_list_to_csv_scalars_and_mixed_keys():
    assert list_to_csv(["a", "b"]).lstrip("﻿").splitlines() == ["value", "a", "b"]
    out = list_to_csv([{"a": 1}, {"b": [1, 2]}]).lstrip("﻿").splitlines()
    assert out == ["a,b", "1,", ',"[1, 2]"']


def test_insert_statement_round_trip_values():
    stmt = insert_statement("t", {"n": 3, "flag": True, "none": None, "data": {"k": "it's"}})
    assert stmt == 'INSERT INTO "t" ("n", "flag", "none", "data") VALUES (3, TRUE, NULL, \'{"k": "it\'\'s"}\'::jsonb);'
    json.loads('{"k": "it\'s"}')
