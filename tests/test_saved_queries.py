"""Tests for the saved-query REST API (``/saved-queries`` routes).

Uses httpx with an ASGITransport against the real FastAPI app, backed by an
in-memory aiosqlite database seeded with the ``saved_queries`` table.
"""

from __future__ import annotations

from typing import Any

import aiosqlite
import httpx
import pytest

from deepferry.core.query_template import extract_params, render, validate_params
from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import app, init_app

# ── helpers ──────────────────────────────────────────────────────────────────

_SAVED_QUERIES_DDL = """
CREATE TABLE IF NOT EXISTS saved_queries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT,
    source_ids   TEXT NOT NULL,
    statement    TEXT NOT NULL,
    tags         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _base_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "my-query",
        "description": "a test",
        "source_ids": ["mysql-main", "pg-analytics"],
        "statement": "SELECT * FROM users WHERE id = {{user_id}}",
        "tags": ["audit", "onboarding"],
    }
    body.update(overrides)
    return body


@pytest.fixture
async def _setup():
    """Yield a fixture that initialises the app with an in-memory SQLite db."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(_SAVED_QUERIES_DDL)
    await db.commit()
    init_app(SourceRegistry(), db=db)
    yield
    await db.close()


# ── CRUD tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_201_with_params_extracted(_setup) -> None:
    async with _make_client() as client:
        resp = await client.post("/saved-queries", json=_base_body())
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-query"
    assert data["source_ids"] == ["mysql-main", "pg-analytics"]
    assert data["params"] == ["user_id"]
    assert data["id"] >= 1


@pytest.mark.asyncio
async def test_create_then_list_includes_created(_setup) -> None:
    async with _make_client() as client:
        await client.post("/saved-queries", json=_base_body())
        resp = await client.get("/saved-queries")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["name"] == "my-query"


@pytest.mark.asyncio
async def test_get_by_id_returns_saved_query(_setup) -> None:
    async with _make_client() as client:
        create_resp = await client.post("/saved-queries", json=_base_body())
        sq_id = create_resp.json()["id"]
        resp = await client.get(f"/saved-queries/{sq_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sq_id


@pytest.mark.asyncio
async def test_get_by_id_404_when_missing(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get("/saved-queries/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_changes_name(_setup) -> None:
    async with _make_client() as client:
        create_resp = await client.post("/saved-queries", json=_base_body())
        sq_id = create_resp.json()["id"]
        resp = await client.put(
            f"/saved-queries/{sq_id}",
            json={"name": "renamed-query"},
        )
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed-query"
    # Other fields unchanged.
    assert resp.json()["source_ids"] == ["mysql-main", "pg-analytics"]


@pytest.mark.asyncio
async def test_update_404_when_missing(_setup) -> None:
    async with _make_client() as client:
        resp = await client.put("/saved-queries/999", json={"name": "nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_preserves_unchanged_fields(_setup) -> None:
    async with _make_client() as client:
        create_resp = await client.post("/saved-queries", json=_base_body())
        sq_id = create_resp.json()["id"]
        # Only update tags.
        resp = await client.put(
            f"/saved-queries/{sq_id}",
            json={"tags": ["a", "b"]},
        )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["a", "b"]
    assert resp.json()["name"] == "my-query"
    assert resp.json()["statement"] == "SELECT * FROM users WHERE id = {{user_id}}"


@pytest.mark.asyncio
async def test_update_with_changed_statement_recomputes_params(_setup) -> None:
    async with _make_client() as client:
        create_resp = await client.post("/saved-queries", json=_base_body())
        sq_id = create_resp.json()["id"]
        resp = await client.put(
            f"/saved-queries/{sq_id}",
            json={"statement": "SELECT {{col}} FROM {{table}} WHERE id = {{user_id}}"},
        )
    assert resp.status_code == 200
    assert resp.json()["params"] == ["col", "table", "user_id"]


@pytest.mark.asyncio
async def test_delete_then_get_404(_setup) -> None:
    async with _make_client() as client:
        create_resp = await client.post("/saved-queries", json=_base_body())
        sq_id = create_resp.json()["id"]
        del_resp = await client.delete(f"/saved-queries/{sq_id}")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"deleted": True}
    async with _make_client() as client:
        get_resp = await client.get(f"/saved-queries/{sq_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_404_when_missing(_setup) -> None:
    async with _make_client() as client:
        resp = await client.delete("/saved-queries/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_name_returns_409(_setup) -> None:
    async with _make_client() as client:
        await client.post("/saved-queries", json=_base_body())
        resp = await client.post("/saved-queries", json=_base_body(name="my-query"))
    assert resp.status_code == 409
    detail = resp.json()
    assert detail["detail"]["code"] == "DUPLICATE_NAME"


@pytest.mark.asyncio
async def test_list_empty_returns_empty_array(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get("/saved-queries")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_updates_tags_correctly(_setup) -> None:
    async with _make_client() as client:
        resp = await client.post(
            "/saved-queries",
            json=_base_body(tags=["a", "b", "c"]),
        )
    assert resp.status_code == 201
    assert resp.json()["tags"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_create_with_no_tags_defaults_to_empty_list(_setup) -> None:
    async with _make_client() as client:
        body = _base_body()
        del body["tags"]
        resp = await client.post("/saved-queries", json=body)
    assert resp.status_code == 201
    assert resp.json()["tags"] == []


@pytest.mark.asyncio
async def test_create_with_no_params_in_statement(_setup) -> None:
    async with _make_client() as client:
        resp = await client.post(
            "/saved-queries",
            json=_base_body(statement="SELECT 1"),
        )
    assert resp.status_code == 201
    assert resp.json()["params"] == []


# ── query_template unit tests ────────────────────────────────────────────────


def test_extract_params_deduplicates():
    assert extract_params("SELECT {{x}}, {{x}} FROM t WHERE y = {{y}}") == ["x", "y"]


def test_extract_params_ordered():
    assert extract_params("SELECT {{c}} FROM {{a}} JOIN {{b}}") == ["c", "a", "b"]


def test_extract_params_empty():
    assert extract_params("SELECT 1") == []


def test_extract_params_ignores_jinja_like():
    """Pea-names like {{ step.x }} are NOT matched — only plain names."""
    assert extract_params("SELECT {{ step.x }}") == []
    assert extract_params("SELECT a {{ for x in y }} b") == []


def test_render_simple_int():
    assert render("WHERE x = {{x}}", {"x": 42}) == "WHERE x = 42"


def test_render_simple_float():
    assert render("WHERE f = {{f}}", {"f": 3.14}) == "WHERE f = 3.14"


def test_render_bool_true():
    assert render("WHERE b = {{b}}", {"b": True}) == "WHERE b = TRUE"


def test_render_bool_false():
    assert render("WHERE b = {{b}}", {"b": False}) == "WHERE b = FALSE"


def test_render_null():
    assert render("WHERE n IS {{n}}", {"n": None}) == "WHERE n IS NULL"


def test_render_string_safe_quoting():
    assert render("WHERE s = {{s}}", {"s": "hello"}) == "WHERE s = 'hello'"


def test_render_string_escapes_quotes():
    assert render("WHERE s = {{s}}", {"s": "it's"}) == "WHERE s = 'it''s'"


def test_render_missing_param_left_verbatim():
    assert render("WHERE x = {{x}} AND y = {{y}}", {"x": 1}) == "WHERE x = 1 AND y = {{y}}"


def test_render_multiple_params():
    result = render(
        "SELECT {{col}} FROM {{table}} WHERE id = {{user_id}}",
        {"col": "name", "table": "users", "user_id": 42},
    )
    assert result == "SELECT 'name' FROM 'users' WHERE id = 42"


def test_validate_params_returns_missing():
    assert validate_params("SELECT {{x}}, {{y}}", {"x": 1}) == ["y"]


def test_validate_params_all_present():
    assert validate_params("SELECT {{x}}, {{y}}", {"x": 1, "y": 2}) == []


def test_validate_params_extra_params_ignored():
    assert validate_params("SELECT {{x}}", {"x": 1, "y": 2}) == []


def test_validate_params_empty_template():
    assert validate_params("SELECT 1", {}) == []
