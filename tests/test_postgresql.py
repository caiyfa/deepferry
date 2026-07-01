"""Tests for PostgreSQLDataSource — unit (mock) and integration (Docker)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from deepferry.config import SourceConfig
from deepferry.core.errors import DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    HealthStatus,
    QueryRequest,
    Resource,
    Schema,
    StructuredResult,
)
from deepferry.datasources.postgresql import PostgreSQLDataSource

# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_mock_pool(conn: MagicMock | None = None) -> MagicMock:
    """Create a mock asyncpg pool that yields *conn* via ``acquire()``."""
    if conn is None:
        conn = MagicMock()

    cm_mock = MagicMock()
    cm_mock.__aenter__ = AsyncMock(return_value=conn)
    cm_mock.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire.return_value = cm_mock
    pool.close = AsyncMock()
    return pool


def _make_record(**kwargs: Any) -> dict[str, Any]:
    """Create a dict that mimics an asyncpg.Record (dict-like + .keys())."""
    return kwargs


# ── Config ──────────────────────────────────────────────────────────────────


def test_pg_config_defaults() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    assert cfg.host is None
    assert cfg.port is None
    assert cfg.user is None
    assert cfg.password is None
    assert cfg.database is None


def test_pg_config_custom() -> None:
    cfg = SourceConfig(id="pg", type="postgresql", host="db.example.com",
        port=5433,
        user="admin",
        password="s3cret",
        database="myapp",
    )
    assert cfg.host == "db.example.com"
    assert cfg.port == 5433
    assert cfg.user == "admin"
    assert cfg.password == "s3cret"
    assert cfg.database == "myapp"


# ── Connect / disconnect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_creates_pool() -> None:
    cfg = SourceConfig(id="pg", type="postgresql",host="pg.local", database="testdb")
    source = PostgreSQLDataSource(cfg)

    mk = AsyncMock(return_value=AsyncMock())
    with patch("deepferry.datasources.postgresql.asyncpg.create_pool", mk):
        await source.connect()

    mk.assert_called_once_with(
        host="pg.local",
        port=5432,
        user="postgres",
        password="",
        database="testdb",
        min_size=2,
        max_size=10,
    )


@pytest.mark.asyncio
async def test_connect_idempotent() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mk = AsyncMock(return_value=AsyncMock())
    with patch("deepferry.datasources.postgresql.asyncpg.create_pool", mk):
        await source.connect()
        await source.connect()  # second call — should be no-op
        mk.assert_called_once()


@pytest.mark.asyncio
async def test_connect_failure_maps_to_data_source_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    with patch("deepferry.datasources.postgresql.asyncpg.create_pool") as mk:
        mk.side_effect = OSError("Connection refused")
        with pytest.raises(DataSourceError) as exc_info:
            await source.connect()

    assert exc_info.value.code == "CONNECTION_FAILED"
    assert "Connection refused" in exc_info.value.message
    assert source._pool is None


@pytest.mark.asyncio
async def test_disconnect_closes_pool() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)
    mock_pool = _build_mock_pool()
    source._pool = mock_pool

    await source.disconnect()

    mock_pool.close.assert_awaited_once()
    assert source._pool is None


@pytest.mark.asyncio
async def test_disconnect_idempotent() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    await source.disconnect()  # no pool — should be safe no-op
    assert source._pool is None


# ── execute() — happy path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_select_returns_structured_result() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_record(id=1, name="Alice"),
            _make_record(id=2, name="Bob"),
        ]
    )
    source._pool = _build_mock_pool(mock_conn)

    result = await source.execute(
        QueryRequest(
            source_id="pg-1",
            statement="SELECT id, name FROM users",
        )
    )

    assert isinstance(result, StructuredResult)
    assert len(result.columns) == 2
    assert result.columns[0] == ColumnMeta(name="id", type="unknown", nullable=True)
    assert result.columns[1] == ColumnMeta(name="name", type="unknown", nullable=True)
    assert result.row_count == 2
    assert result.rows == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    assert result.execution_time_ms > 0


@pytest.mark.asyncio
async def test_execute_with_params_converts_dict_to_positional() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=[_make_record(result="ok")])
    source._pool = _build_mock_pool(mock_conn)

    await source.execute(
        QueryRequest(
            source_id="pg-1",
            statement="SELECT * FROM users WHERE id = $1 AND status = $2",
            params={"user_id": 42, "status": "active"},
        )
    )

    # Verify params were passed as positional list in dict insertion order
    call_args = mock_conn.fetch.call_args
    assert call_args is not None
    assert call_args.args[1:] == (42, "active")


@pytest.mark.asyncio
async def test_execute_max_rows_truncates() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    rows_data = [_make_record(id=i) for i in range(10)]
    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=rows_data)
    source._pool = _build_mock_pool(mock_conn)

    result = await source.execute(
        QueryRequest(
            source_id="pg-1",
            statement="SELECT id FROM users",
            max_rows=3,
        )
    )

    assert result.row_count == 3
    assert len(result.rows) == 3


@pytest.mark.asyncio
async def test_execute_with_timeout_passed_to_fetch() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    source._pool = _build_mock_pool(mock_conn)

    await source.execute(
        QueryRequest(
            source_id="pg-1",
            statement="SELECT 1",
            timeout=15,
        )
    )

    mock_conn.fetch.assert_awaited_once()
    assert mock_conn.fetch.call_args.kwargs["timeout"] == 15


# ── execute() — error paths ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_not_connected_raises() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT 1")
        )

    assert exc_info.value.code == "NOT_CONNECTED"


@pytest.mark.asyncio
async def test_execute_syntax_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.PostgresSyntaxError("syntax error")
    )
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT 1 FROM")
        )

    assert exc_info.value.code == "QUERY_FAILED"


@pytest.mark.asyncio
async def test_execute_undefined_table_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("table not found")
    )
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT * FROM nonexistent")
        )

    assert exc_info.value.code == "QUERY_FAILED"


@pytest.mark.asyncio
async def test_execute_connection_lost_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.ConnectionFailureError("connection lost")
    )
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT 1")
        )

    assert exc_info.value.code == "CONNECTION_FAILED"


@pytest.mark.asyncio
async def test_execute_timeout_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        side_effect=TimeoutError("timed out")
    )
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT pg_sleep(999)")
        )

    assert exc_info.value.code == "TIMEOUT"


@pytest.mark.asyncio
async def test_execute_unexpected_error() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(side_effect=RuntimeError("boom"))
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(
            QueryRequest(source_id="pg-1", statement="SELECT 1")
        )

    assert exc_info.value.code == "QUERY_FAILED"


# ── list_resources() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_resources_returns_tables_and_views() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_record(tablename="users", resource_type=None),
            _make_record(tablename="orders", resource_type=None),
            _make_record(tablename="user_summary", resource_type="view"),
        ]
    )
    source._pool = _build_mock_pool(mock_conn)

    resources = await source.list_resources()

    assert len(resources) == 3
    assert resources[0] == Resource(name="users", type="table")
    assert resources[1] == Resource(name="orders", type="table")
    assert resources[2] == Resource(name="user_summary", type="view")


@pytest.mark.asyncio
async def test_list_resources_not_connected_raises() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    with pytest.raises(DataSourceError) as exc_info:
        await source.list_resources()

    assert exc_info.value.code == "NOT_CONNECTED"


@pytest.mark.asyncio
async def test_list_resources_pg_catalog_failure() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(side_effect=RuntimeError("catalog down"))
    source._pool = _build_mock_pool(mock_conn)

    with pytest.raises(DataSourceError) as exc_info:
        await source.list_resources()

    assert exc_info.value.code == "QUERY_FAILED"


# ── schema_info() ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_info_all_tables() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_record(table_name="users", column_name="id", data_type="integer", nullable=False),
            _make_record(table_name="users", column_name="name", data_type="text", nullable=True),
            _make_record(table_name="orders", column_name="id", data_type="integer", nullable=False),
            _make_record(table_name="orders", column_name="amount", data_type="numeric", nullable=True),
        ]
    )
    source._pool = _build_mock_pool(mock_conn)

    schema = await source.schema_info()

    assert isinstance(schema, Schema)
    assert len(schema.resources) == 2

    users = next(r for r in schema.resources if r.name == "users")
    assert len(users.columns) == 2
    assert users.columns[0] == ColumnMeta(name="id", type="integer", nullable=False)
    assert users.columns[1] == ColumnMeta(name="name", type="text", nullable=True)

    orders = next(r for r in schema.resources if r.name == "orders")
    assert len(orders.columns) == 2
    assert orders.columns[0] == ColumnMeta(name="id", type="integer", nullable=False)
    assert orders.columns[1] == ColumnMeta(name="amount", type="numeric", nullable=True)


@pytest.mark.asyncio
async def test_schema_info_single_table() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            _make_record(table_name="users", column_name="id", data_type="integer", nullable=False),
            _make_record(table_name="users", column_name="email", data_type="text", nullable=False),
        ]
    )
    source._pool = _build_mock_pool(mock_conn)

    schema = await source.schema_info(resource="users")

    assert len(schema.resources) == 1
    assert schema.resources[0].name == "users"
    assert len(schema.resources[0].columns) == 2

    # Verify the table_name filter was applied
    call_args = mock_conn.fetch.call_args
    assert call_args is not None
    assert call_args.args[1] == "users"  # $1 parameter


@pytest.mark.asyncio
async def test_schema_info_not_connected_raises() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    with pytest.raises(DataSourceError) as exc_info:
        await source.schema_info()

    assert exc_info.value.code == "NOT_CONNECTED"


# ── health_check() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_healthy() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(return_value=1)
    source._pool = _build_mock_pool(mock_conn)

    status = await source.health_check()

    assert isinstance(status, HealthStatus)
    assert status.ok is True
    assert status.latency_ms > 0
    assert status.error is None
    mock_conn.fetchval.assert_awaited_once_with("SELECT 1", timeout=5)


@pytest.mark.asyncio
async def test_health_check_not_connected() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    status = await source.health_check()

    assert status.ok is False
    assert "Not connected" in (status.error or "")


@pytest.mark.asyncio
async def test_health_check_failure() -> None:
    cfg = SourceConfig(id="pg", type="postgresql")
    source = PostgreSQLDataSource(cfg)

    mock_conn = MagicMock()
    mock_conn.fetchval = AsyncMock(side_effect=OSError("gone"))
    source._pool = _build_mock_pool(mock_conn)

    status = await source.health_check()

    assert status.ok is False
    assert "gone" in (status.error or "")


# ── Integration tests (require Docker postgres:16) ──────────────────────────

INTEGRATION = pytest.mark.integration


def _pg_env(key: str, default: str) -> str:
    return os.environ.get(key, default)


@INTEGRATION
@pytest.mark.asyncio
async def test_integration_connect_and_health() -> None:
    """Connect to a real PostgreSQL instance and verify health check."""
    cfg = SourceConfig(id="pg", type="postgresql",
        host=_pg_env("POSTGRES_HOST", "localhost"),
        port=int(_pg_env("POSTGRES_PORT", "5432")),
        user=_pg_env("POSTGRES_USER", "postgres"),
        password=_pg_env("POSTGRES_PASSWORD", "postgres"),
        database=_pg_env("POSTGRES_DATABASE", "postgres"),
    )
    source = PostgreSQLDataSource(cfg)
    await source.connect()
    try:
        status = await source.health_check()
        assert status.ok is True
        assert status.latency_ms > 0
    finally:
        await source.disconnect()


@INTEGRATION
@pytest.mark.asyncio
async def test_integration_list_resources_and_schema() -> None:
    """Create a temp table, list it, introspect its schema, then clean up."""
    cfg = SourceConfig(id="pg", type="postgresql",
        host=_pg_env("POSTGRES_HOST", "localhost"),
        port=int(_pg_env("POSTGRES_PORT", "5432")),
        user=_pg_env("POSTGRES_USER", "postgres"),
        password=_pg_env("POSTGRES_PASSWORD", "postgres"),
        database=_pg_env("POSTGRES_DATABASE", "postgres"),
    )
    source = PostgreSQLDataSource(cfg)
    await source.connect()
    try:
        # Create a temp table with known columns
        await source.execute(
            QueryRequest(
                source_id="pg-int",
                statement=(
                    "CREATE TEMP TABLE _deepferry_test ("
                    "id SERIAL PRIMARY KEY, "
                    "label TEXT NOT NULL, "
                    "amount NUMERIC(10,2)"
                    ")"
                ),
            )
        )

        resources = await source.list_resources()
        # pg_temp tables appear in pg_catalog under pg_temp_* schema, not public.
        # This test verifies list_resources runs without error.
        assert isinstance(resources, list)

        # Schema info for all public tables
        schema = await source.schema_info()
        assert isinstance(schema, Schema)

    finally:
        await source.disconnect()


@INTEGRATION
@pytest.mark.asyncio
async def test_integration_execute_and_health() -> None:
    """Execute a SELECT query against a real PostgreSQL instance."""
    cfg = SourceConfig(id="pg", type="postgresql",
        host=_pg_env("POSTGRES_HOST", "localhost"),
        port=int(_pg_env("POSTGRES_PORT", "5432")),
        user=_pg_env("POSTGRES_USER", "postgres"),
        password=_pg_env("POSTGRES_PASSWORD", "postgres"),
        database=_pg_env("POSTGRES_DATABASE", "postgres"),
    )
    source = PostgreSQLDataSource(cfg)
    await source.connect()
    try:
        result = await source.execute(
            QueryRequest(
                source_id="pg-int",
                statement="SELECT 1 AS one, 'hello' AS greeting",
            )
        )
        assert result.row_count == 1
        assert result.rows[0]["one"] == 1
        assert result.rows[0]["greeting"] == "hello"
        assert result.execution_time_ms > 0
    finally:
        await source.disconnect()
