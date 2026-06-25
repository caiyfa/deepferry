"""Tests for MySQLDataSource — unit tests with mocks + integration test structure.

Unit tests mock asyncmy to verify the full lifecycle, error handling, and
result formatting without a real MySQL server.  Integration tests require
Docker (mysql:8) and are skipped by default.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepferry.config import SourceConfig
from deepferry.core.errors import DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    QueryRequest,
    Resource,
    Schema,
    StructuredResult,
)
from deepferry.datasources.mysql import MySQLDataSource, _map_mysql_type

# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(**overrides: Any) -> SourceConfig:
    """Build a SourceConfig with sensible MySQL defaults."""
    defaults: dict[str, Any] = {
        "id": "test-mysql",
        "type": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "testpass",
        "database": "testdb",
    }
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _mock_async_context_manager(return_value: Any) -> MagicMock:
    """Create a MagicMock whose ``__aenter__`` returns *return_value*."""
    mgr = MagicMock()
    mgr.__aenter__ = AsyncMock(return_value=return_value)
    mgr.__aexit__ = AsyncMock(return_value=None)
    return mgr


@dataclass
class _MockCursor:
    """Simulates an asyncmy cursor with configurable description + rows."""

    description: tuple[tuple[Any, ...], ...] | None = None
    rows: list[tuple[Any, ...]] | None = None
    execute: Any = None
    fetchall: Any = None

    def __post_init__(self) -> None:
        if self.execute is None:
            self.execute = AsyncMock(return_value=None)
        if self.fetchall is None:
            self.fetchall = AsyncMock(return_value=self.rows or [])


def _setup_mock_pool(
    cursor: _MockCursor,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Wire up a mock pool → connection → cursor chain.

    Returns (mock_pool, mock_conn, mock_cursor) so tests can assert on them.
    """
    mock_cursor = MagicMock()
    mock_cursor.execute = cursor.execute
    mock_cursor.fetchall = cursor.fetchall
    mock_cursor.description = cursor.description or ()

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)

    return mock_pool, mock_conn, mock_cursor


# ── Type mapping ─────────────────────────────────────────────────────


class TestMapMySQLType:
    def test_known_type(self) -> None:
        assert _map_mysql_type(3) == "INT"
        assert _map_mysql_type(253) == "VARCHAR"
        assert _map_mysql_type(12) == "DATETIME"

    def test_unknown_type(self) -> None:
        assert _map_mysql_type(999) == "UNKNOWN(999)"


# ── Source type ──────────────────────────────────────────────────────


class TestSourceType:
    def test_source_type_classvar(self) -> None:
        assert MySQLDataSource.source_type == "mysql"


# ── Connect / Disconnect ─────────────────────────────────────────────


class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_creates_pool(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        mock_pool = MagicMock()
        with patch("asyncmy.create_pool", new=AsyncMock(return_value=mock_pool)) as create:
            await source.connect()

        create.assert_awaited_once_with(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="testpass",
            database="testdb",
            minsize=2,
            maxsize=10,
        )
        assert source._pool is mock_pool

    @pytest.mark.asyncio
    async def test_connect_idempotent(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        mock_pool = MagicMock()
        with patch("asyncmy.create_pool", new=AsyncMock(return_value=mock_pool)) as create:
            await source.connect()
            await source.connect()  # second call

        create.assert_awaited_once()  # pool created only once

    @pytest.mark.asyncio
    async def test_connect_failure(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        from asyncmy.errors import OperationalError

        with patch(
            "asyncmy.create_pool",
            new=AsyncMock(side_effect=OperationalError("Connection refused")),
        ):
            with pytest.raises(DataSourceError) as exc_info:
                await source.connect()
            assert exc_info.value.code == "CONNECTION_FAILED"
            assert "Connection refused" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_disconnect_closes_pool(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        mock_pool = MagicMock()
        mock_pool.close = MagicMock()
        mock_pool.wait_closed = AsyncMock()
        source._pool = mock_pool

        await source.disconnect()

        mock_pool.close.assert_called_once()
        mock_pool.wait_closed.assert_awaited_once()
        assert source._pool is None

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        source._pool = None

        await source.disconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_disconnect_none_pool(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        await source.disconnect()  # no-op when _pool is None


# ── Execute ──────────────────────────────────────────────────────────


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_select(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            description=(
                ("id", 3, None, None, None, None, 0),
                ("name", 253, None, None, None, None, 1),
            ),
            rows=[(1, "alpha"), (2, "beta")],
        )
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        result = await source.execute(
            QueryRequest(source_id="test-mysql", statement="SELECT id, name FROM t")
        )

        assert isinstance(result, StructuredResult)
        assert result.row_count == 2
        assert result.execution_time_ms > 0
        assert len(result.columns) == 2
        assert result.columns[0] == ColumnMeta(name="id", type="INT", nullable=False)
        assert result.columns[1] == ColumnMeta(name="name", type="VARCHAR", nullable=True)
        assert result.rows == [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
        ]

    @pytest.mark.asyncio
    async def test_execute_with_params(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            description=(("val", 253, None, None, None, None, 0),),
            rows=[("x",)],
        )
        mock_pool, _, mock_cursor = _setup_mock_pool(cursor)
        source._pool = mock_pool

        await source.execute(
            QueryRequest(
                source_id="test-mysql",
                statement="SELECT val FROM t WHERE id = %(pk)s",
                params={"pk": 42},
            )
        )

        mock_cursor.execute.assert_awaited_once_with(
            "SELECT val FROM t WHERE id = %(pk)s", {"pk": 42}
        )

    @pytest.mark.asyncio
    async def test_execute_no_rows(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            description=(("id", 3, None, None, None, None, 0),),
            rows=[],
        )
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        result = await source.execute(
            QueryRequest(source_id="test-mysql", statement="SELECT id FROM t WHERE 1=0")
        )

        assert result.row_count == 0
        assert result.rows == []
        assert len(result.columns) == 1

    @pytest.mark.asyncio
    async def test_execute_ddl_no_description(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(description=None, rows=[])
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        result = await source.execute(
            QueryRequest(source_id="test-mysql", statement="CREATE TABLE foo (id INT)")
        )

        assert result.columns == []
        assert result.rows == []
        assert result.row_count == 0

    @pytest.mark.asyncio
    async def test_execute_query_failed(self) -> None:
        from asyncmy.errors import ProgrammingError

        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(
            side_effect=ProgrammingError("Table 'testdb.nope' doesn't exist")
        )
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-mysql", statement="SELECT * FROM nope")
            )
        assert exc_info.value.code == "QUERY_FAILED"

    @pytest.mark.asyncio
    async def test_execute_connection_failed_during_query(self) -> None:
        from asyncmy.errors import OperationalError

        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(
            side_effect=OperationalError("MySQL server has gone away")
        )
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-mysql", statement="SELECT 1")
            )
        assert exc_info.value.code == "CONNECTION_FAILED"

    @pytest.mark.asyncio
    async def test_execute_timeout(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-mysql", statement="SELECT SLEEP(999)", timeout=1)
            )
        assert exc_info.value.code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_execute_not_connected(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        # _pool is None by default

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-mysql", statement="SELECT 1")
            )
        assert exc_info.value.code == "NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_execute_integrity_error(self) -> None:
        from asyncmy.errors import IntegrityError

        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(
            side_effect=IntegrityError("Duplicate entry '1' for key 'PRIMARY'")
        )
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        with pytest.raises(DataSourceError) as exc_info:
            await source.execute(
                QueryRequest(source_id="test-mysql", statement="INSERT INTO t VALUES (1)")
            )
        assert exc_info.value.code == "QUERY_FAILED"


# ── List resources ───────────────────────────────────────────────────


class TestListResources:
    @pytest.mark.asyncio
    async def test_list_resources_tables_and_views(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            rows=[
                ("users", "BASE TABLE"),
                ("orders", "BASE TABLE"),
                ("user_summary", "VIEW"),
            ],
        )
        mock_pool, _, mock_cursor = _setup_mock_pool(cursor)
        source._pool = mock_pool

        resources = await source.list_resources()

        assert len(resources) == 3
        assert resources[0] == Resource(name="users", type="table")
        assert resources[1] == Resource(name="orders", type="table")
        assert resources[2] == Resource(name="user_summary", type="view")
        mock_cursor.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_resources_empty(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(rows=[])
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        resources = await source.list_resources()
        assert resources == []

    @pytest.mark.asyncio
    async def test_list_resources_not_connected(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        with pytest.raises(DataSourceError) as exc_info:
            await source.list_resources()
        assert exc_info.value.code == "NOT_CONNECTED"

    @pytest.mark.asyncio
    async def test_list_resources_connection_failed(self) -> None:
        from asyncmy.errors import OperationalError

        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(
            side_effect=OperationalError("Access denied for information_schema")
        )
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        with pytest.raises(DataSourceError) as exc_info:
            await source.list_resources()
        assert exc_info.value.code == "CONNECTION_FAILED"


# ── Schema info ──────────────────────────────────────────────────────


class TestSchemaInfo:
    @pytest.mark.asyncio
    async def test_schema_info_all_tables(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            rows=[
                ("users", "id", "int", "NO"),
                ("users", "name", "varchar", "YES"),
                ("orders", "id", "int", "NO"),
                ("orders", "total", "decimal", "YES"),
            ],
        )
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        schema = await source.schema_info()

        assert isinstance(schema, Schema)
        assert len(schema.resources) == 2  # users, orders
        users = next(r for r in schema.resources if r.name == "users")
        assert len(users.columns) == 2
        assert users.columns[0] == ColumnMeta(name="id", type="int", nullable=False)
        assert users.columns[1] == ColumnMeta(name="name", type="varchar", nullable=True)

    @pytest.mark.asyncio
    async def test_schema_info_filtered(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(
            rows=[
                ("users", "id", "int", "NO"),
                ("users", "name", "varchar", "YES"),
            ],
        )
        mock_pool, _, mock_cursor = _setup_mock_pool(cursor)
        source._pool = mock_pool

        schema = await source.schema_info(resource="users")

        assert len(schema.resources) == 1
        assert schema.resources[0].name == "users"
        # Verify the filtered query was used
        call_args = mock_cursor.execute.call_args
        assert "TABLE_NAME = %s" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_schema_info_empty(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor(rows=[])
        mock_pool, _, _ = _setup_mock_pool(cursor)
        source._pool = mock_pool

        schema = await source.schema_info(resource="nonexistent")
        assert schema.resources == []

    @pytest.mark.asyncio
    async def test_schema_info_not_connected(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        with pytest.raises(DataSourceError) as exc_info:
            await source.schema_info()
        assert exc_info.value.code == "NOT_CONNECTED"


# ── Health check ─────────────────────────────────────────────────────


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_healthy(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)
        cursor = _MockCursor()
        mock_pool, _, mock_cursor = _setup_mock_pool(cursor)
        source._pool = mock_pool

        health = await source.health_check()

        assert health.ok is True
        assert health.latency_ms > 0
        assert health.error is None
        mock_cursor.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_health_check_not_connected(self) -> None:
        config = _make_config()
        source = MySQLDataSource(config)

        health = await source.health_check()

        assert health.ok is False
        assert health.latency_ms == 0
        assert health.error is not None
        assert "Not connected" in health.error

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self) -> None:
        from asyncmy.errors import OperationalError

        config = _make_config()
        source = MySQLDataSource(config)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute = AsyncMock(
            side_effect=OperationalError("Lost connection")
        )
        mock_conn.cursor.return_value = _mock_async_context_manager(mock_cursor)
        mock_pool.acquire.return_value = _mock_async_context_manager(mock_conn)
        source._pool = mock_pool

        health = await source.health_check()

        assert health.ok is False
        assert health.error is not None
        assert "Lost connection" in health.error


# ── Integration tests (require Docker mysql:8) ───────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("MYSQL_HOST") and not os.environ.get("CI"),
    reason="Integration tests require a running MySQL instance",
)
class TestIntegration:
    """Integration tests against a real MySQL instance.

    Start with::

        docker run -d --name mysql-test \\
            -e MYSQL_ROOT_PASSWORD=testpass \\
            -e MYSQL_DATABASE=testdb \\
            -p 3306:3306 mysql:8

    Set environment variables to override defaults:
    - MYSQL_HOST (default localhost)
    - MYSQL_PORT (default 3306)
    - MYSQL_USER (default root)
    - MYSQL_PASSWORD (default testpass)
    - MYSQL_DATABASE (default testdb)
    """

    @pytest.fixture
    async def connected_source(self) -> AsyncGenerator[MySQLDataSource, None]:
        config = SourceConfig(
            id="test-mysql-int",
            type="mysql",
            host=os.environ.get("MYSQL_HOST", "localhost"),
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ.get("MYSQL_USER", "root"),
            password=os.environ.get("MYSQL_PASSWORD", "testpass"),
            database=os.environ.get("MYSQL_DATABASE", "testdb"),
        )
        source = MySQLDataSource(config)
        await source.connect()
        try:
            yield source
        finally:
            await source.disconnect()

    @pytest.mark.asyncio
    async def test_integration_health_check(
        self, connected_source: MySQLDataSource
    ) -> None:
        health = await connected_source.health_check()
        assert health.ok is True
        assert health.latency_ms > 0

    @pytest.mark.asyncio
    async def test_integration_execute_select(
        self, connected_source: MySQLDataSource
    ) -> None:
        result = await connected_source.execute(
            QueryRequest(source_id="test-mysql-int", statement="SELECT 1 AS one")
        )
        assert result.row_count == 1
        assert result.rows[0]["one"] == 1
        assert result.columns[0].name == "one"

    @pytest.mark.asyncio
    async def test_integration_execute_with_params(
        self, connected_source: MySQLDataSource
    ) -> None:
        result = await connected_source.execute(
            QueryRequest(
                source_id="test-mysql-int",
                statement="SELECT %(a)s + %(b)s AS total",
                params={"a": 10, "b": 20},
            )
        )
        assert result.rows[0]["total"] == 30

    @pytest.mark.asyncio
    async def test_integration_list_resources(
        self, connected_source: MySQLDataSource
    ) -> None:
        resources = await connected_source.list_resources()
        assert isinstance(resources, list)

    @pytest.mark.asyncio
    async def test_integration_schema_info(
        self, connected_source: MySQLDataSource
    ) -> None:
        schema = await connected_source.schema_info()
        assert isinstance(schema, Schema)
        assert isinstance(schema.resources, list)

    @pytest.mark.asyncio
    async def test_integration_execute_error(
        self, connected_source: MySQLDataSource
    ) -> None:
        with pytest.raises(DataSourceError) as exc_info:
            await connected_source.execute(
                QueryRequest(
                    source_id="test-mysql-int",
                    statement="SELECT * FROM __nonexistent_table_xyz__",
                )
            )
        assert exc_info.value.code == "QUERY_FAILED"
