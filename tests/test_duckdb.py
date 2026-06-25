from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deepferry.config import SourceConfig
from deepferry.core.errors import DataSourceError
from deepferry.core.models import ColumnMeta, QueryRequest, StructuredResult
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import SourceRegistry, register_source_type
from deepferry.engine.duckdb import (
    DuckDBEngine,
    _build_attach_string,
    _parse_source_refs,
    _to_duckdb_type,
    _transform_sql,
)


class FakeMySQLSource(DataSource):
    source_type = "mysql"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__()
        self._config = config

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass
    async def execute(self, query: QueryRequest) -> StructuredResult:
        raise NotImplementedError
    async def list_resources(self): return []
    async def schema_info(self, resource=None): raise NotImplementedError
    async def health_check(self): raise NotImplementedError


class FakeHTTPSource(DataSource):
    source_type = "http"

    def __init__(self, config: SourceConfig) -> None:
        super().__init__()
        self._config = config
        self._execute_mock: Any = None

    async def connect(self) -> None: pass
    async def disconnect(self) -> None: pass

    async def execute(self, query: QueryRequest) -> StructuredResult:
        if self._execute_mock:
            result = self._execute_mock(query)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return StructuredResult(
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=0,
        )

    async def list_resources(self): return []
    async def schema_info(self, resource=None): raise NotImplementedError
    async def health_check(self): raise NotImplementedError


def _make_sql_config(source_id: str, **overrides: Any) -> SourceConfig:
    base: dict[str, Any] = {
        "id": source_id,
        "type": "mysql",
        "host": "localhost",
        "port": 3306,
        "database": "testdb",
        "user": "root",
        "password": "secret",
    }
    base.update(overrides)
    return SourceConfig(**base)


def _make_http_config(source_id: str) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        type="http",
        base_url="https://api.example.com",
    )


# ── Registry helpers ───────────────────────────────────────────────────


@pytest.fixture
def registry_with_sql():
    register_source_type("mysql", FakeMySQLSource)
    reg = SourceRegistry()
    cfg = _make_sql_config("mysql_src")
    source = FakeMySQLSource(cfg)
    source.source_id = "mysql_src"
    reg._instances["mysql_src"] = source
    return reg


@pytest.fixture
def registry_with_http():
    register_source_type("http", FakeHTTPSource)
    reg = SourceRegistry()
    cfg = _make_http_config("http_src")
    source = FakeHTTPSource(cfg)
    source.source_id = "http_src"
    reg._instances["http_src"] = source
    return reg


# ── Tests ──────────────────────────────────────────────────────────────


class TestDuckDBEngineLifecycle:
    async def test_connect_disconnect(self):
        registry = SourceRegistry()
        engine = DuckDBEngine(registry)
        await engine.connect()
        assert engine._conn is not None
        # Verify in-memory connection works
        result = engine._conn.execute("SELECT 1 AS one").fetchone()
        assert result[0] == 1
        await engine.disconnect()
        assert engine._conn is None

    async def test_execute_without_connect_raises(self, registry_with_sql):
        engine = DuckDBEngine(registry_with_sql)
        query = QueryRequest(source_id="__cross__", statement="SELECT 1")
        with pytest.raises(DataSourceError, match="not connected"):
            await engine.execute(query, registry_with_sql)


class TestParseSourceRefs:
    def test_extracts_sql_source_refs(self, registry_with_sql):
        sql = "SELECT * FROM mysql_src.customers JOIN mysql_src.orders"
        refs = _parse_source_refs(sql, registry_with_sql)
        assert refs["sql"] == {"mysql_src"}
        assert refs["http"] == {}

    def test_extracts_http_source_refs(self, registry_with_http):
        sql = "SELECT * FROM http_src.orders"
        refs = _parse_source_refs(sql, registry_with_http)
        assert refs["sql"] == set()
        assert refs["http"] == {"http_src": {"orders"}}

    def test_mixed_sources(self, registry_with_sql, registry_with_http):
        reg = SourceRegistry()
        reg._instances = {
            **registry_with_sql._instances,
            **registry_with_http._instances,
        }
        sql = "SELECT * FROM mysql_src.customers c JOIN http_src.orders o ON c.id = o.user_id"
        refs = _parse_source_refs(sql, reg)
        assert refs["sql"] == {"mysql_src"}
        assert refs["http"] == {"http_src": {"orders"}}

    def test_ignores_unknown_sources(self, registry_with_sql):
        sql = "SELECT * FROM unknown_src.foo"
        refs = _parse_source_refs(sql, registry_with_sql)
        assert refs["sql"] == set()
        assert refs["http"] == {}

    def test_ignores_quoted_identifiers(self, registry_with_sql):
        sql = "SELECT 'mysql_src.customers' AS label FROM mysql_src.orders"
        refs = _parse_source_refs(sql, registry_with_sql)
        assert refs["sql"] == {"mysql_src"}
        assert refs["http"] == {}


class TestBuildAttachString:
    def test_mysql_attach_string(self):
        cfg = _make_sql_config("mysql_src")
        result = _build_attach_string(cfg)
        assert "host=localhost" in result
        assert "port=3306" in result
        assert "user=root" in result
        assert "password=secret" in result
        assert "database=testdb" in result

    def test_postgresql_attach_string(self):
        cfg = _make_sql_config("pg_src", type="postgresql", port=5432)
        result = _build_attach_string(cfg)
        assert "host=localhost" in result
        assert "port=5432" in result
        assert "dbname=testdb" in result


class TestTypeMapping:
    def test_known_types(self):
        assert _to_duckdb_type("BIGINT") == "BIGINT"
        assert _to_duckdb_type("INT") == "INTEGER"
        assert _to_duckdb_type("INTEGER") == "INTEGER"
        assert _to_duckdb_type("VARCHAR") == "VARCHAR"
        assert _to_duckdb_type("TEXT") == "VARCHAR"
        assert _to_duckdb_type("BOOLEAN") == "BOOLEAN"
        assert _to_duckdb_type("DOUBLE") == "DOUBLE"

    def test_unknown_type_falls_back_to_varchar(self):
        assert _to_duckdb_type("GEOMETRY") == "VARCHAR"

    def test_number_maps_to_double(self):
        assert _to_duckdb_type("NUMBER") == "DOUBLE"
        assert _to_duckdb_type("number") == "DOUBLE"


class TestHTTPMaterialization:
    async def test_materialize_http_to_duckdb(self):
        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        cfg = _make_http_config("orders_api")
        source = FakeHTTPSource(cfg)
        source.source_id = "orders_api"

        async def mock_execute(query: QueryRequest) -> StructuredResult:
            return StructuredResult(
                columns=[
                    ColumnMeta(name="order_id", type="BIGINT"),
                    ColumnMeta(name="amount", type="DOUBLE"),
                ],
                rows=[
                    {"order_id": 1, "amount": 99.99},
                    {"order_id": 2, "amount": 149.50},
                ],
                row_count=2,
                execution_time_ms=5.0,
            )

        source._execute_mock = mock_execute
        reg._instances["orders_api"] = source

        engine = DuckDBEngine(reg)
        await engine.connect()

        await engine._materialize_http_sources(
            {"orders_api": {"orders"}}, reg
        )

        result = engine._conn.execute(
            "SELECT * FROM orders_api.orders"
        ).fetchall()
        assert len(result) == 2
        assert result[0][0] == 1
        assert result[0][1] == 99.99

        await engine.disconnect()


class TestCrossSourceJoin:
    async def test_in_memory_cross_source_join(self):
        """Simulate cross-source JOIN using DuckDB temp tables.

        This test mimics what happens when one SQL source and one HTTP source
        are both materialized into DuckDB and then JOINed.
        """
        engine = DuckDBEngine(SourceRegistry())
        await engine.connect()

        # Simulate MySQL-side table materialized into DuckDB
        engine._conn.execute("""
            CREATE TEMP TABLE mysql_src_customers (
                id INTEGER, name VARCHAR, email VARCHAR
            )
        """)
        engine._conn.execute("""
            INSERT INTO mysql_src_customers VALUES
            (1, 'Alice', 'alice@example.com'),
            (2, 'Bob', 'bob@example.com'),
            (3, 'Carol', 'carol@example.com')
        """)

        # Simulate HTTP-side orders materialized into DuckDB
        engine._conn.execute("""
            CREATE TEMP TABLE orders_api_orders (
                order_id INTEGER, user_id INTEGER, amount DOUBLE
            )
        """)
        engine._conn.execute("""
            INSERT INTO orders_api_orders VALUES
            (101, 1, 99.99),
            (102, 1, 49.50),
            (103, 2, 199.99)
        """)

        # Cross-source JOIN
        result = engine._conn.execute("""
            SELECT c.name, SUM(o.amount) AS total_spent
            FROM mysql_src_customers c
            JOIN orders_api_orders o ON c.id = o.user_id
            GROUP BY c.name
            ORDER BY total_spent DESC
        """).fetchall()

        assert len(result) == 2
        assert result[0][0] == "Bob"
        assert result[0][1] == 199.99

        await engine.disconnect()

    async def test_union_across_sources(self):
        engine = DuckDBEngine(SourceRegistry())
        await engine.connect()

        engine._conn.execute("""
            CREATE TEMP TABLE src_a (id INTEGER, name VARCHAR)
        """)
        engine._conn.execute("INSERT INTO src_a VALUES (1, 'Alice'), (2, 'Bob')")

        engine._conn.execute("""
            CREATE TEMP TABLE src_b (id INTEGER, name VARCHAR)
        """)
        engine._conn.execute("INSERT INTO src_b VALUES (3, 'Carol'), (4, 'Dave')")

        result = engine._conn.execute("""
            SELECT * FROM src_a UNION ALL SELECT * FROM src_b ORDER BY id
        """).fetchall()

        assert len(result) == 4
        assert result[2][1] == "Carol"

        await engine.disconnect()


class TestSafetyChecks:
    async def test_rejects_select_without_limit(self):
        reg = SourceRegistry()
        engine = DuckDBEngine(reg)
        await engine.connect()

        query = QueryRequest(
            source_id="__cross__",
            statement="SELECT * FROM t",
        )
        with pytest.raises(DataSourceError, match="without LIMIT"):
            await engine.execute(query, reg)

        await engine.disconnect()

    async def test_allows_select_with_limit(self):
        reg = SourceRegistry()
        engine = DuckDBEngine(reg)
        await engine.connect()

        engine._conn.execute("CREATE TEMP TABLE t (id INTEGER, name VARCHAR)")
        engine._conn.execute("INSERT INTO t VALUES (1, 'Alice')")

        query = QueryRequest(
            source_id="__cross__",
            statement="SELECT * FROM t LIMIT 10",
        )
        result = await engine.execute(query, reg)
        assert result.row_count == 1
        assert result.rows[0]["name"] == "Alice"

        await engine.disconnect()

    async def test_allows_aggregation_without_limit(self):
        reg = SourceRegistry()
        engine = DuckDBEngine(reg)
        await engine.connect()

        engine._conn.execute("CREATE TEMP TABLE t (id INTEGER, amount DOUBLE)")
        engine._conn.execute("INSERT INTO t VALUES (1, 10.0), (2, 20.0)")

        query = QueryRequest(
            source_id="__cross__",
            statement="SELECT COUNT(*) AS cnt FROM t",
        )
        result = await engine.execute(query, reg)
        assert result.row_count == 1

        await engine.disconnect()


class TestCrossQueryTool:
    async def test_cross_query_returns_structured_result(self):
        from deepferry.mcp_server.tools import cross_query

        register_source_type("http", FakeHTTPSource)
        reg = SourceRegistry()
        cfg = _make_http_config("data_src")
        source = FakeHTTPSource(cfg)
        source.source_id = "data_src"

        async def mock_execute(query: QueryRequest) -> StructuredResult:
            return StructuredResult(
                columns=[
                    ColumnMeta(name="id", type="BIGINT"),
                    ColumnMeta(name="value", type="VARCHAR"),
                ],
                rows=[{"id": 1, "value": "hello"}],
                row_count=1,
                execution_time_ms=3.0,
            )

        source._execute_mock = mock_execute
        reg._instances["data_src"] = source

        engine = DuckDBEngine(reg)
        await engine.connect()

        result = await cross_query(
            registry=reg,
            engine=engine,
            sql="SELECT * FROM data_src.items LIMIT 1",
        )

        assert result.source_id == "__cross__"
        assert result.row_count == 1
        assert result.rows[0]["value"] == "hello"

        await engine.disconnect()

    async def test_cross_query_handles_engine_error(self):
        from deepferry.mcp_server.tools import cross_query

        reg = SourceRegistry()
        engine = DuckDBEngine(reg)
        # Don't connect - should fail

        with pytest.raises(DataSourceError, match="not connected"):
            await cross_query(
                registry=reg,
                engine=engine,
                sql="SELECT 1",
            )


class TestParseSourceRefsQuotedAndHyphen:
    def test_quoted_sql_source_id(self, registry_with_sql):
        sql = 'SELECT * FROM "mysql_src".customers'
        refs = _parse_source_refs(sql, registry_with_sql)
        assert refs["sql"] == {"mysql_src"}

    def test_hyphenated_sql_source_id(self):
        reg = SourceRegistry()
        cfg = _make_sql_config("finance-db")
        source = FakeMySQLSource(cfg)
        source.source_id = "finance-db"
        reg._instances["finance-db"] = source
        sql = 'SELECT * FROM "finance-db".employee'
        refs = _parse_source_refs(sql, reg)
        assert refs["sql"] == {"finance-db"}

    def test_hyphenated_http_source_id(self):
        reg = SourceRegistry()
        cfg = _make_http_config("orders-api")
        source = FakeHTTPSource(cfg)
        source.source_id = "orders-api"
        reg._instances["orders-api"] = source
        sql = 'SELECT * FROM "orders-api".orders'
        refs = _parse_source_refs(sql, reg)
        assert refs["http"] == {"orders-api": {"orders"}}


class TestTransformSql:
    def test_two_part_becomes_three_part(self, registry_with_sql):
        sql = 'SELECT * FROM "mysql_src".customers'
        refs = _parse_source_refs(sql, registry_with_sql)
        result = _transform_sql(sql, refs, registry_with_sql)
        assert '"mysql_src"."testdb".customers' in result

    def test_hyphenated_source_rewrite(self):
        reg = SourceRegistry()
        cfg = _make_sql_config("finance-db")
        source = FakeMySQLSource(cfg)
        source.source_id = "finance-db"
        reg._instances["finance-db"] = source
        sql = (
            'SELECT * FROM "finance-db".employee e '
            'JOIN "finance-db".orders o ON o.id = e.id'
        )
        refs = _parse_source_refs(sql, reg)
        result = _transform_sql(sql, refs, reg)
        assert '"finance-db"."testdb".employee' in result
        assert '"finance-db"."testdb".orders' in result

    def test_http_sources_not_rewritten(self, registry_with_http):
        sql = "SELECT * FROM http_src.orders"
        refs = _parse_source_refs(sql, registry_with_http)
        result = _transform_sql(sql, refs, registry_with_http)
        assert result == sql
