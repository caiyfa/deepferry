"""Unit tests for the LLM SQL guard module (datasource-agnostic safety validation).

Covers allowed SQL (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH), blocked SQL
(write keywords, stacked queries), and edge cases (empty input, false-positive
prevention on column names like ``updated_at``).
"""

from __future__ import annotations

import pytest

from deepferry.core.errors import DataSourceError
from deepferry.core.llm_guard import (
    _DANGEROUS_KEYWORDS,  # noqa: PLC2701
    scan_sql,
)

# ── Allowed SQL ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT 1",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "SHOW TABLES",
        "DESCRIBE users",
        "EXPLAIN SELECT * FROM t",
        "EXPLAIN ANALYZE SELECT * FROM t",
        "  SELECT 1",
        "select id from t",
        "WITH\n  a AS (SELECT 1),\n  b AS (SELECT 2)\nSELECT * FROM a, b",
    ],
)
def test_scan_sql_allows_valid_read_only_sql(sql: str) -> None:
    """scan_sql should not raise for allowed read-only statements."""
    scan_sql(sql)


# ── Blocked SQL (keyword detection) ────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE users",
        "INSERT INTO t VALUES(1)",
        "DELETE FROM t",
        "UPDATE t SET x=1",
        "ALTER TABLE t ADD c INT",
        "CREATE TABLE t(id INT)",
        "TRUNCATE TABLE t",
        "GRANT SELECT ON t TO u",
        "REPLACE INTO t VALUES(1)",
        "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET x=1",
        "EXEC sp_foo",
        "EXECUTE sp_foo",
        "CALL sp_foo()",
    ],
)
def test_scan_sql_rejects_dangerous_keywords(sql: str) -> None:
    """scan_sql should raise WRITE_BLOCKED for dangerous write keywords."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(sql)
    assert exc_info.value.code == "WRITE_BLOCKED"
    assert any(kw in exc_info.value.message for kw in _DANGEROUS_KEYWORDS)


# ── Stacked queries ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE users",
        "SELECT 1; INSERT INTO t VALUES(1)",
        "SHOW TABLES; DELETE FROM users",
        "WITH cte AS (SELECT 1) SELECT * FROM cte; DROP TABLE x",
        "select 1; truncate table t",
        "SELECT 1; UPDATE t SET x=1",
    ],
)
def test_scan_sql_rejects_stacked_queries(sql: str) -> None:
    """scan_sql should catch dangerous keywords in stacked queries."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(sql)
    assert exc_info.value.code == "WRITE_BLOCKED"


# ── Edge cases: empty / whitespace ─────────────────────────────────────────


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", "\n", "\t\t"])
def test_scan_sql_rejects_empty_input(sql: str) -> None:
    """scan_sql should raise INVALID_SQL for empty or whitespace-only input."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(sql)
    assert exc_info.value.code == "INVALID_SQL"


# ── Edge cases: non-read prefixes ──────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SET autocommit=1",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "USE testdb",
        "PRAGMA foreign_keys=ON",
    ],
)
def test_scan_sql_rejects_non_read_prefixes(sql: str) -> None:
    """scan_sql should raise INVALID_SQL for statements that don't start with an allowed prefix."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(sql)
    assert exc_info.value.code == "INVALID_SQL"


# ── Edge cases: false-positive prevention ──────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM updated_at",
        "SELECT name, created_at, updated_at FROM users",
        "SELECT * FROM drop_shipping_orders",
        "SELECT inserted_at FROM events",
        "SELECT deleted_flag FROM records",
        "SELECT InsertDate FROM legacy_table",
        "SELECT * FROM caller_ids",
        "SELECT execution_plan FROM queries",
    ],
)
def test_scan_sql_no_false_positives_on_column_names(sql: str) -> None:
    """Word-boundary regex should not flag column names that contain dangerous keywords as substrings."""
    scan_sql(sql)


# ── Edge cases: real-world LLM-generated SQL ───────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'",
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'users' ORDER BY ordinal_position",
        "SELECT tc.table_name, kcu.column_name, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'",
        "WITH RECURSIVE hierarchies AS (SELECT id, parent_id, name, 0 AS depth FROM categories WHERE parent_id IS NULL UNION ALL SELECT c.id, c.parent_id, c.name, h.depth + 1 FROM categories c JOIN hierarchies h ON c.parent_id = h.id) SELECT * FROM hierarchies",
        "SHOW INDEX FROM users",
        "EXPLAIN SELECT * FROM orders WHERE status = 'pending' AND EXECUTED_AT IS NULL",
    ],
)
def test_scan_sql_allows_real_world_llm_queries(sql: str) -> None:
    """Real-world LLM-generated exploration queries should pass validation."""
    scan_sql(sql)
