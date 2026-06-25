"""Tests for HTTPDataSource — unit tests using mocked httpx2 responses.

Covers the full lifecycle (connect → execute → disconnect), JSON flattening,
schema inference, body-template interpolation, error handling, and
production safeguards.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import httpx2
import pytest

from deepferry.auth.token_manager import TokenManager
from deepferry.config import SourceConfig
from deepferry.core.errors import DataSourceError
from deepferry.core.models import QueryRequest
from deepferry.datasources.http_api import (
    HTTPDataSource,
    _find_array,
    _flatten_response,
    _render_body_template,
)


def _make_config(**overrides: Any) -> SourceConfig:
    base: dict[str, Any] = {
        "id": "test-http",
        "type": "http",
        "base_url": "https://api.example.com/v1",
        "extra": {
            "resources": [
                {
                    "name": "users",
                    "path": "/users",
                    "method": "GET",
                    "description": "List all users",
                },
                {
                    "name": "create_user",
                    "path": "/users",
                    "method": "POST",
                    "body_template": {"name": "{{name}}", "email": "{{email}}"},
                    "description": "Create a user",
                },
            ],
            "default_headers": {"Accept": "application/json"},
        },
    }
    base.update(overrides)
    extra = base.pop("extra", {})
    merged: dict[str, Any] = {**base}
    for k, v in extra.items():
        if k not in SourceConfig._reserved_top_level:
            merged["extra"] = {**merged.get("extra", {}), k: v}
    return SourceConfig(**merged)


def _make_config_extra(**extra_overrides: Any) -> SourceConfig:
    cfg = _make_config()
    existing = cfg.extra.copy()
    existing.update(extra_overrides)
    return SourceConfig(
        id=cfg.id, type=cfg.type, base_url=cfg.base_url, extra=existing,
    )


def _mock_client():
    client = MagicMock()
    client.aclose = AsyncMock()
    client.request = AsyncMock()
    client.get = AsyncMock()
    return client


def _resp(status=200, body=None, headers=None):
    if headers is None:
        headers = {}
    if "content-type" not in headers:
        headers["content-type"] = "application/json"
    raw = json.dumps(body).encode() if body is not None else b"{}"

    def _json():
        return body if body is not None else {}

    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers
    resp.text = raw.decode()
    resp.content = raw
    resp.json = _json
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _err_resp(status, body="", headers=None):
    if headers is None:
        headers = {}
    raw = body.encode()
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers
    resp.text = body
    resp.content = raw
    resp.json = MagicMock(return_value={})
    resp.raise_for_status = MagicMock(side_effect=httpx2.HTTPStatusError(
        f"HTTP {status}", request=MagicMock(), response=resp,
    ))
    return resp


def _set_client(source, client):
    source._client = client


# JSON flattening

class TestFindArray:
    def test_detects_root_array(self):
        assert _find_array([{"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]

    def test_detects_data_key(self):
        assert _find_array({"data": [{"id": 1}]}) == [{"id": 1}]

    def test_detects_items_key(self):
        assert _find_array({"items": [{"id": 1}]}) == [{"id": 1}]

    def test_detects_results_key(self):
        assert _find_array({"results": [{"id": 1}]}) == [{"id": 1}]

    def test_detects_records_key(self):
        assert _find_array({"records": [{"id": 1}]}) == [{"id": 1}]

    def test_detects_first_list_value(self):
        assert _find_array({"payload": [{"id": 1}]}) == [{"id": 1}]

    def test_empty_on_plain_dict(self):
        assert _find_array({"a": 1}) == []

    def test_empty_on_non_dict_non_list(self):
        assert _find_array(42) == []


class TestFlattenResponse:
    def test_flattens_nested_object(self):
        data = {
            "data": [
                {"id": 1, "user": {"name": "Alice", "email": "a@b.com"}},
                {"id": 2, "user": {"name": "Bob"}},
            ]
        }
        columns, rows = _flatten_response(data)
        col_names = {c.name for c in columns}
        assert col_names == {"id", "user.name", "user.email"}
        assert rows[0]["user.name"] == "Alice"
        assert rows[0]["user.email"] == "a@b.com"
        assert "user.email" not in rows[1]

    def test_flattens_root_array(self):
        data = [{"id": 1, "info": {"city": "NYC"}}]
        columns, rows = _flatten_response(data)
        col_names = {c.name for c in columns}
        assert col_names == {"id", "info.city"}
        assert rows[0]["info.city"] == "NYC"

    def test_empty_on_empty_object(self):
        columns, rows = _flatten_response({})
        assert columns == []
        assert rows == []

    def test_empty_on_no_array(self):
        columns, rows = _flatten_response({"status": "ok"})
        assert columns == []
        assert rows == []

    def test_scalar_items_wrapped(self):
        columns, rows = _flatten_response({"data": [1, 2, 3]})
        assert len(columns) == 1
        assert columns[0].name == "value"
        assert rows == [{"value": 1}, {"value": 2}, {"value": 3}]

    def test_nested_list_marked_complex(self):
        columns, rows = _flatten_response({"data": [{"id": 1, "tags": ["a", "b"]}]})
        col_names = {c.name for c in columns}
        assert "tags" in col_names
        assert rows[0]["tags"] == "[complex]"


# Body template interpolation

class TestRenderBodyTemplate:
    def test_resolves_from_params(self):
        result = _render_body_template(
            {"name": "{{name}}", "email": "{{email}}"},
            {"name": "Alice", "email": "a@b.com"},
        )
        assert result == {"name": "Alice", "email": "a@b.com"}

    def test_raises_on_unresolved(self):
        with pytest.raises(DataSourceError) as exc_info:
            _render_body_template({"name": "{{name}}"}, {})
        assert exc_info.value.code == "INVALID_BINDING"

    def test_none_template_returns_none(self):
        assert _render_body_template(None, {}) is None

    def test_preserves_non_string_values(self):
        assert _render_body_template({"count": 42}, {}) == {"count": 42}


# Lifecycle

class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_creates_client(self):
        source = HTTPDataSource(_make_config())
        assert source._client is None
        await source.connect()
        assert source._client is not None
        await source.disconnect()

    @pytest.mark.asyncio
    async def test_connect_is_idempotent(self):
        source = HTTPDataSource(_make_config())
        await source.connect()
        c1 = source._client
        await source.connect()
        assert source._client is c1
        await source.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_is_idempotent(self):
        source = HTTPDataSource(_make_config())
        await source.connect()
        await source.disconnect()
        assert source._client is None
        await source.disconnect()


# Resource listing

class TestListResources:
    @pytest.mark.asyncio
    async def test_returns_configured_endpoints(self):
        source = HTTPDataSource(_make_config())
        await source.connect()
        resources = await source.list_resources()
        assert len(resources) == 2
        assert resources[0].name == "users"
        assert resources[0].type == "endpoint"
        assert resources[0].description == "List all users"

    @pytest.mark.asyncio
    async def test_empty_when_no_resources_configured(self):
        config = _make_config(extra={"resources": []})
        source = HTTPDataSource(config)
        await source.connect()
        assert await source.list_resources() == []


# Execute

class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_get_flattens_json(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _resp(body={"data": [{"id": 1, "name": "Alice"}]})
        _set_client(source, client)

        result = await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert result.row_count == 1
        assert result.rows[0]["id"] == 1
        assert result.rows[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_execute_get_with_params(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _resp(body={"data": [{"id": 5}]})
        _set_client(source, client)

        await source.execute(QueryRequest(
            source_id="test-http", statement="users", params={"status": "active"},
        ))
        assert client.request.call_args.kwargs["url"] == "/users?status=active"

    @pytest.mark.asyncio
    async def test_execute_post_body_template(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _resp(body={"data": [{"id": 99}]})
        _set_client(source, client)

        result = await source.execute(QueryRequest(
            source_id="test-http", statement="create_user",
            params={"name": "Alice", "email": "a@b.com"},
        ))
        assert client.request.call_args.kwargs["json"] == {"name": "Alice", "email": "a@b.com"}
        assert result.rows[0]["id"] == 99

    @pytest.mark.asyncio
    async def test_execute_resource_not_found(self):
        source = HTTPDataSource(_make_config())
        _set_client(source, _mock_client())
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="nonexistent"))
        assert exc_info.value.code == "RESOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_execute_http_client_error(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _err_resp(404)
        _set_client(source, client)
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "HTTP_CLIENT_ERROR"

    @pytest.mark.asyncio
    async def test_execute_http_server_error(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _err_resp(500)
        _set_client(source, client)
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "HTTP_SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request = AsyncMock(side_effect=httpx2.TimeoutException("timed out"))
        _set_client(source, client)
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_execute_connection_error(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request = AsyncMock(side_effect=httpx2.ConnectError("refused"))
        _set_client(source, client)
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "CONNECTION_FAILED"

    @pytest.mark.asyncio
    async def test_execute_not_connected(self):
        source = HTTPDataSource(_make_config())
        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_execute_applies_max_rows(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.request.return_value = _resp(body={"data": [{"id": i} for i in range(10)]})
        _set_client(source, client)

        result = await source.execute(QueryRequest(
            source_id="test-http", statement="users", max_rows=3,
        ))
        assert result.row_count == 3

    @pytest.mark.asyncio
    async def test_execute_rejects_large_content_length(self):
        source = HTTPDataSource(_make_config_extra(max_response_bytes=100))
        client = _mock_client()
        resp = _resp(body={"data": []})
        resp.headers["content-length"] = "500"
        client.request.return_value = resp
        _set_client(source, client)

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(QueryRequest(source_id="test-http", statement="users"))
        assert exc_info.value.code == "RESPONSE_TOO_LARGE"


# Schema info

class TestSchemaInfo:
    @pytest.mark.asyncio
    async def test_infers_types_from_sample(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get.return_value = _resp(body={"data": [
            {"id": 1, "name": "Alice", "active": True},
        ]})
        _set_client(source, client)

        schema = await source.schema_info()
        assert len(schema.resources) == 2
        col_types = {c.name: c.type for c in schema.resources[0].columns}
        assert col_types["id"] == "integer"
        assert col_types["name"] == "string"
        assert col_types["active"] == "boolean"

    @pytest.mark.asyncio
    async def test_specific_resource(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get.return_value = _resp(body={"data": [{"id": 1}]})
        _set_client(source, client)

        schema = await source.schema_info(resource="users")
        assert len(schema.resources) == 1
        assert schema.resources[0].name == "users"

    @pytest.mark.asyncio
    async def test_resource_not_found(self):
        source = HTTPDataSource(_make_config())
        _set_client(source, _mock_client())
        with pytest.raises(DataSourceError) as exc_info:
            await source.schema_info(resource="nonexistent")
        assert exc_info.value.code == "RESOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_uses_explicit_columns(self):
        config = _make_config_extra(resources=[{
            "name": "typed",
            "path": "/typed",
            "method": "GET",
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "label", "type": "string", "nullable": False},
            ],
        }])
        source = HTTPDataSource(config)
        client = _mock_client()
        _set_client(source, client)

        schema = await source.schema_info()
        cols = schema.resources[0].columns
        assert cols[0].name == "id"
        assert cols[0].type == "integer"
        assert cols[1].nullable is False

    @pytest.mark.asyncio
    async def test_survives_endpoint_error(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        resp = _resp(status=500)
        resp.raise_for_status.side_effect = httpx2.HTTPStatusError(
            "boom", request=MagicMock(), response=resp,
        )
        client.get.return_value = resp
        _set_client(source, client)

        schema = await source.schema_info()
        assert len(schema.resources) == 2
        assert any(c.name == "error" for r in schema.resources for c in r.columns)


# Health check

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_on_200(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get.return_value = _resp(status=200)
        _set_client(source, client)

        status = await source.health_check()
        assert status.ok is True
        assert status.latency_ms > 0

    @pytest.mark.asyncio
    async def test_healthy_on_302(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get.return_value = _resp(status=302)
        _set_client(source, client)

        status = await source.health_check()
        assert status.ok is True

    @pytest.mark.asyncio
    async def test_unhealthy_on_500(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get.return_value = _resp(status=500)
        _set_client(source, client)

        status = await source.health_check()
        assert status.ok is False
        assert status.error is not None

    @pytest.mark.asyncio
    async def test_unhealthy_on_connect_error(self):
        source = HTTPDataSource(_make_config())
        client = _mock_client()
        client.get = AsyncMock(side_effect=httpx2.ConnectError("refused"))
        _set_client(source, client)

        status = await source.health_check()
        assert status.ok is False
        assert "refused" in str(status.error)

    @pytest.mark.asyncio
    async def test_not_connected(self):
        source = HTTPDataSource(_make_config())
        status = await source.health_check()
        assert status.ok is False
        assert status.error is not None


_AUTH_EXTRA: dict[str, Any] = {
    "login_url": "https://api.example.com/auth/login",
    "login_method": "POST",
    "login_body": {"username": "u", "password": "p"},
    "token_field": "access_token",
    "token_type": "bearer",
}


async def _make_auth_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS token_cache (
            source_id      TEXT PRIMARY KEY,
            access_token   TEXT NOT NULL,
            refresh_token  TEXT,
            token_type     TEXT NOT NULL DEFAULT 'bearer',
            expires_at     REAL NOT NULL,
            extra          TEXT,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.commit()
    return db


def _mock_login_response(access_token: str = "fresh-token") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"access_token": access_token, "expires_in": 3600}
    resp.raise_for_status.return_value = None
    return resp


class TestAuthRetry:
    @pytest.mark.asyncio
    async def test_auth_retry_on_401(self):
        """First request 401 → invalidate → re-login → retry succeeds (200)."""
        db = await _make_auth_db()
        future = time.time() + 3600
        await db.execute(
            "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
            "VALUES (?, ?, 'bearer', ?)",
            ("test-http-auth", "initial-token", future),
        )
        await db.commit()

        auth_http = AsyncMock(spec=httpx2.AsyncClient)
        auth_http.request.return_value = _mock_login_response("fresh-token")

        tm = TokenManager(db=db, http_client=auth_http)

        cfg = _make_config_extra(id="test-http-auth", auth=_AUTH_EXTRA)
        source = HTTPDataSource(cfg, token_manager=tm)
        source.source_id = "test-http-auth"

        data_client = _mock_client()
        data_client.request.side_effect = [
            _resp(status=401),
            _resp(body={"data": [{"id": 1, "name": "Alice"}]}),
        ]
        _set_client(source, data_client)

        result = await source.execute(
            QueryRequest(source_id="test-http-auth", statement="users"),
        )
        assert result.row_count == 1
        assert result.rows[0]["id"] == 1
        assert data_client.request.call_count == 2
        assert auth_http.request.call_count >= 1

        await db.close()

    @pytest.mark.asyncio
    async def test_auth_retry_fails_on_double_401(self):
        """Both original and retry return 401 → AUTH_FAILED."""
        db = await _make_auth_db()
        future = time.time() + 3600
        await db.execute(
            "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
            "VALUES (?, ?, 'bearer', ?)",
            ("test-http-auth", "initial-token", future),
        )
        await db.commit()

        auth_http = AsyncMock(spec=httpx2.AsyncClient)
        auth_http.request.return_value = _mock_login_response("fresh-token")

        tm = TokenManager(db=db, http_client=auth_http)

        cfg = _make_config_extra(id="test-http-auth", auth=_AUTH_EXTRA)
        source = HTTPDataSource(cfg, token_manager=tm)
        source.source_id = "test-http-auth"

        data_client = _mock_client()
        data_client.request.side_effect = [
            _resp(status=401),
            _resp(status=401),
        ]
        _set_client(source, data_client)

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-http-auth", statement="users"),
            )
        assert exc_info.value.code == "AUTH_FAILED"
        assert data_client.request.call_count == 2

        await db.close()

    @pytest.mark.asyncio
    async def test_auth_retry_preserves_concurrent_safety(self):
        """5 concurrent 401s → login called exactly once, all retries succeed."""
        db = await _make_auth_db()
        future = time.time() + 3600
        await db.execute(
            "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
            "VALUES (?, ?, 'bearer', ?)",
            ("test-http-auth", "initial-token", future),
        )
        await db.commit()

        login_count = 0

        async def counted_login(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal login_count
            login_count += 1
            await asyncio.sleep(0.03)
            return _mock_login_response(f"token-{login_count}")

        auth_http = AsyncMock(spec=httpx2.AsyncClient)
        auth_http.request.side_effect = counted_login

        tm = TokenManager(db=db, http_client=auth_http)

        cfg = _make_config_extra(id="test-http-auth", auth=_AUTH_EXTRA)

        async def run_one() -> None:
            source = HTTPDataSource(cfg, token_manager=tm)
            source.source_id = "test-http-auth"

            data_client = _mock_client()
            data_client.request.side_effect = [
                _resp(status=401),
                _resp(body={"data": [{"id": 1}]}),
            ]
            _set_client(source, data_client)

            await source.execute(
                QueryRequest(source_id="test-http-auth", statement="users"),
            )

        await asyncio.gather(*(run_one() for _ in range(5)))

        assert login_count == 1, f"Expected 1 login, got {login_count}"

        await db.close()
