"""Unit tests for query safeguards (read-only enforcement).

Covers MySQL and PostgreSQL datasource read-only checks: allowed
statements (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) and rejected
write operations (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE).
"""

from __future__ import annotations

import pytest

from deepferry.datasources.mysql import MySQLDataSource
from deepferry.datasources.postgresql import PostgreSQLDataSource

# ── _is_read_only tests ───────────────────────────────────────────────────


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
    assert MySQLDataSource._is_read_only(statement) is True


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
    assert MySQLDataSource._is_read_only(statement) is False


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
    assert PostgreSQLDataSource._is_read_only(statement) is True


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
    assert PostgreSQLDataSource._is_read_only(statement) is False


@pytest.mark.parametrize(
    "statement",
    [
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive t as (select 1) select * from t",
        "WITH\n  a AS (SELECT 1),\n  b AS (SELECT 2)\nSELECT * FROM a, b",
    ],
)
def test_with_cte_allowed_mysql(statement: str) -> None:
    assert MySQLDataSource._is_read_only(statement) is True


@pytest.mark.parametrize(
    "statement",
    [
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive t as (select 1) select * from t",
        "WITH\n  a AS (SELECT 1),\n  b AS (SELECT 2)\nSELECT * FROM a, b",
    ],
)
def test_with_cte_allowed_postgresql(statement: str) -> None:
    assert PostgreSQLDataSource._is_read_only(statement) is True


# ── Edge cases ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "",
        "   ",
        "\n\t",
    ],
)
def test_empty_statements_mysql(statement: str) -> None:
    assert MySQLDataSource._is_read_only(statement) is False


@pytest.mark.parametrize(
    "statement",
    [
        "SET autocommit=1",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "USE testdb",
    ],
)
def test_non_read_statement_classification_mysql(statement: str) -> None:
    assert MySQLDataSource._is_read_only(statement) is False


@pytest.mark.parametrize(
    "statement",
    [
        "SET search_path TO public",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
    ],
)
def test_non_read_statement_classification_postgresql(statement: str) -> None:
    assert PostgreSQLDataSource._is_read_only(statement) is False
