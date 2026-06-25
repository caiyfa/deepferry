"""End-to-end tests against a real MySQL Docker container."""
from __future__ import annotations

import json
import os

import pytest

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.toml")


@pytest.fixture(scope="module")
async def registry():
    from deepferry.config import load_config
    from deepferry.datasources.mysql import MySQLDataSource
    from deepferry.datasources.registry import SourceRegistry, register_source_type
    register_source_type("mysql", MySQLDataSource)

    config = load_config(CONFIG_PATH)
    reg = SourceRegistry()
    await reg.load_from_config(config)
    yield reg
    await reg.shutdown()


@pytest.fixture
async def source(registry):
    return registry.get("deepferry-mysql")


@pytest.mark.asyncio(loop_scope="module")
async def test_list_sources(registry):
    sources = registry.list_sources()
    assert len(sources) == 1
    assert sources[0].id == "deepferry-mysql"


@pytest.mark.asyncio(loop_scope="module")
async def test_list_tables(source):
    resources = await source.list_resources()
    names = {r.name for r in resources}
    assert names >= {"customers", "order_items", "orders", "product_reviews", "products"}


@pytest.mark.asyncio(loop_scope="module")
async def test_schema_info_all(source):
    schema = await source.schema_info()
    assert len(schema.resources) >= 5
    customers = next(r for r in schema.resources if r.name == "customers")
    cols = {c.name for c in customers.columns}
    assert "id" in cols and "email" in cols and "metadata" in cols


@pytest.mark.asyncio(loop_scope="module")
async def test_schema_info_single_table(source):
    schema = await source.schema_info(resource="products")
    assert len(schema.resources) == 1
    assert schema.resources[0].name == "products"


@pytest.mark.asyncio(loop_scope="module")
async def test_query_select(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement="SELECT id,name,status,credit_limit FROM customers ORDER BY id",
    ))
    assert r.row_count == 7
    assert r.execution_time_ms > 0
    assert r.rows[0]["name"] == "Alice Johnson"
    assert r.rows[0]["status"] == "vip"


@pytest.mark.asyncio(loop_scope="module")
async def test_query_params(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement="SELECT name FROM customers WHERE status = %s",
        params={"s": "vip"},
    ))
    assert r.row_count == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_query_aggregation(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement=(
            "SELECT c.name, COUNT(o.id) AS cnt, COALESCE(SUM(o.total_amount),0) AS total "
            "FROM customers c LEFT JOIN orders o ON c.id=o.customer_id "
            "AND o.status!='cancelled' GROUP BY c.id, c.name ORDER BY total DESC"
        ),
    ))
    assert r.rows[0]["name"] == "Eve Brown"
    assert r.rows[0]["cnt"] == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_query_subquery(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement="SELECT p.name FROM products p "
                  "WHERE p.id NOT IN (SELECT DISTINCT product_id FROM product_reviews)",
    ))
    assert r.row_count == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_query_json(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement="SELECT name, JSON_EXTRACT(metadata, '$.tags') AS tags "
                  "FROM customers WHERE metadata IS NOT NULL",
    ))
    alice = next(row for row in r.rows if row["name"] == "Alice Johnson")
    assert "enterprise" in str(alice["tags"])


@pytest.mark.asyncio(loop_scope="module")
async def test_query_date_range(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement="SELECT status, COUNT(*) AS cnt FROM orders "
                  "WHERE order_date >= %s AND order_date < %s GROUP BY status",
        params={"start": "2026-06-01", "end": "2026-07-01"},
    ))
    assert sum(row["cnt"] for row in r.rows) == 9


@pytest.mark.asyncio(loop_scope="module")
async def test_query_max_rows(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x", statement="SELECT * FROM order_items ORDER BY id", max_rows=3,
    ))
    assert r.row_count == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_query_multi_join(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement=(
            "SELECT p.name, COUNT(DISTINCT oi.order_id) AS ordered, SUM(oi.quantity) AS sold "
            "FROM products p LEFT JOIN order_items oi ON p.id=oi.product_id "
            "LEFT JOIN orders o ON oi.order_id=o.id AND o.status!='cancelled' "
            "GROUP BY p.id, p.name ORDER BY sold DESC"
        ),
    ))
    assert r.row_count == 10


@pytest.mark.asyncio(loop_scope="module")
async def test_query_having(source):
    from deepferry.core.models import QueryRequest

    r = await source.execute(QueryRequest(
        source_id="x",
        statement=(
            "SELECT c.name, COUNT(o.id) AS cnt FROM customers c "
            "JOIN orders o ON c.id=o.customer_id WHERE o.status!='cancelled' "
            "GROUP BY c.id, c.name HAVING COUNT(o.id) > 1 ORDER BY cnt DESC"
        ),
    ))
    assert r.row_count == 4


@pytest.mark.asyncio(loop_scope="module")
async def test_health(source):
    h = await source.health_check()
    assert h.ok is True
    assert h.latency_ms > 0


@pytest.mark.asyncio(loop_scope="module")
async def test_mcp_tools(registry):
    from deepferry.mcp_server.tools import execute_query, list_sources, list_tables, schema_info

    sources = await list_sources(registry)
    assert len(sources) == 1

    tables = await list_tables(registry, "deepferry-mysql")
    assert any(t.name == "customers" for t in tables)

    schema = await schema_info(registry, "deepferry-mysql", table="orders")
    assert schema.resources[0].name == "orders"

    qr = await execute_query(
        registry, "deepferry-mysql",
        "SELECT COUNT(*) AS n FROM customers WHERE status = %s",
        params={"s": "vip"},
    )
    assert qr.rows[0]["n"] == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_json_serializable(registry):
    from deepferry.mcp_server.tools import execute_query

    qr = await execute_query(
        registry, "deepferry-mysql",
        "SELECT id, name, credit_limit FROM customers WHERE id = %s",
        params={"id": 1},
    )
    data = json.loads(json.dumps(qr.model_dump(mode="json"), default=str))
    assert data["rows"][0]["name"] == "Alice Johnson"


@pytest.mark.asyncio(loop_scope="module")
async def test_error_handling(source):
    from deepferry.core.errors import DataSourceError
    from deepferry.core.models import QueryRequest

    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(QueryRequest(
            source_id="x", statement="SELECT * FROM nonexistent"))
    assert exc_info.value.code == "QUERY_FAILED"


@pytest.mark.asyncio(loop_scope="module")
async def test_source_not_found(registry):
    from deepferry.core.errors import SourceNotFoundError

    with pytest.raises(SourceNotFoundError):
        registry.get("nonexistent")
