"""Unit tests for query safeguards (read-only enforcement).

Covers both the datasource-level ``_scan_sql`` static methods (dangerous
keyword detection) and the shared ``llm_guard.scan_sql`` function (full
validation including empty/prefix checks).
"""

from __future__ import annotations

import pytest

from deepferry.core.errors import DataSourceError
from deepferry.core.llm_guard import scan_sql
from deepferry.datasources.mysql import MySQLDataSource
from deepferry.datasources.postgresql import PostgreSQLDataSource

# ── Datasource-level _scan_sql tests ────────────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1",
        "SELECT * FROM users",
        "select id, name from t",
        "  SELECT 1",
        "SHOW TABLES",
        "SHOW DATABASES",
        "DESCRIBE users",
        "EXPLAIN SELECT 1",
        "EXPLAIN ANALYZE SELECT * FROM t",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive t as (select 1) select * from t",
    ],
)
def test_read_only_statements_allowed_mysql(statement: str) -> None:
    """Datasource-level _scan_sql should not raise for safe read-only statements."""
    MySQLDataSource._scan_sql(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET name = 'x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN x INT",
        "CREATE TABLE foo (id INT)",
        "TRUNCATE TABLE users",
        "GRANT SELECT ON users TO 'u'",
    ],
)
def test_write_statements_rejected_mysql(statement: str) -> None:
    """Datasource-level _scan_sql must raise WRITE_BLOCKED for dangerous keywords."""
    with pytest.raises(DataSourceError) as exc_info:
        MySQLDataSource._scan_sql(statement)
    assert exc_info.value.code == "WRITE_BLOCKED"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1",
        "SELECT * FROM users",
        "select id, name from t",
        "  SELECT 1",
        "SHOW search_path",
        "EXPLAIN SELECT 1",
        "EXPLAIN ANALYZE SELECT * FROM t",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
    ],
)
def test_read_only_statements_allowed_postgresql(statement: str) -> None:
    PostgreSQLDataSource._scan_sql(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET name = 'x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN x INT",
        "CREATE TABLE foo (id INT)",
        "TRUNCATE TABLE users",
        "GRANT SELECT ON users TO 'u'",
        "REPLACE INTO users VALUES (1)",
    ],
)
def test_write_statements_rejected_postgresql(statement: str) -> None:
    with pytest.raises(DataSourceError) as exc_info:
        PostgreSQLDataSource._scan_sql(statement)
    assert exc_info.value.code == "WRITE_BLOCKED"


@pytest.mark.parametrize(
    "statement",
    [
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive t as (select 1) select * from t",
        "WITH\n  a AS (SELECT 1),\n  b AS (SELECT 2)\nSELECT * FROM a, b",
    ],
)
def test_with_cte_allowed_mysql(statement: str) -> None:
    MySQLDataSource._scan_sql(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive t as (select 1) select * from t",
        "WITH\n  a AS (SELECT 1),\n  b AS (SELECT 2)\nSELECT * FROM a, b",
    ],
)
def test_with_cte_allowed_postgresql(statement: str) -> None:
    PostgreSQLDataSource._scan_sql(statement)


# ── Shared llm_guard.scan_sql edge case tests ───────────────────────────
# The datasource-level _scan_sql only checks dangerous keywords.  The shared
# llm_guard.scan_sql additionally validates that SQL starts with an allowed
# prefix and is not empty.  Edge cases below use the shared function.


@pytest.mark.parametrize(
    "statement",
    [
        "",
        "   ",
        "\n\t",
    ],
)
def test_empty_statements_rejected(statement: str) -> None:
    """Empty / whitespace-only SQL must raise INVALID_SQL."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(statement)
    assert exc_info.value.code == "INVALID_SQL"


@pytest.mark.parametrize(
    "statement",
    [
        "SET autocommit=1",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "USE testdb",
        "SET search_path TO public",
    ],
)
def test_non_read_statement_classification(statement: str) -> None:
    """Statements not starting with an allowed prefix must raise INVALID_SQL."""
    with pytest.raises(DataSourceError) as exc_info:
        scan_sql(statement)
    assert exc_info.value.code == "INVALID_SQL"
