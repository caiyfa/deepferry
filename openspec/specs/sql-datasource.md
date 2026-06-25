# Capability: SQL Data Sources (MySQL + PostgreSQL)

> **Status**: planned | **Milestone**: M1 | **Owner**: backend

## Summary

Implement MySQL and PostgreSQL data source drivers using async database drivers, both conforming to the `DataSource` abstract interface.

## Motivation

- MySQL and PostgreSQL are the two most popular open-source relational databases
- Having both in M1 proves the DataSource abstraction works for homogeneous SQL sources
- Different async drivers (asyncmy vs asyncpg) demonstrate driver-agnostic design

## Specification

### MySQL Implementation

```python
class MySQLDataSource(DataSource):
    def __init__(self, config: MySQLConfig):
        self._pool: asyncmy.Pool | None = None
        self._config = config

    async def connect(self) -> None:
        self._pool = await asyncmy.create_pool(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            password=self._config.password,
            database=self._config.database,
            minsize=2,
            maxsize=10,
        )

    async def execute(self, query: QueryRequest) -> StructuredResult:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query.statement)
                rows = await cursor.fetchall()
                columns = [ColumnMeta(name=d[0], type=d[1]) for d in cursor.description]
                return StructuredResult(
                    columns=columns,
                    rows=[dict(zip([c.name for c in columns], row)) for row in rows],
                    row_count=len(rows),
                    execution_time_ms=...,
                )

    async def list_resources(self) -> list[Resource]:
        # SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = ?
        ...

    async def schema_info(self, resource: str | None = None) -> Schema:
        # SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM information_schema.COLUMNS
        ...
```

### PostgreSQL Implementation

Same interface, different driver (asyncpg), SQL dialect differences:

```python
class PostgreSQLDataSource(DataSource):
    # Uses asyncpg instead of asyncmy
    # list_tables: SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'
    # schema_info: SELECT column_name, data_type, is_nullable FROM information_schema.columns
```

### SQL Dialect Handling

Common SQL dialect differences handled transparently:

| Operation | MySQL | PostgreSQL |
|-----------|-------|------------|
| List tables | `SHOW TABLES` | `SELECT tablename FROM pg_catalog.pg_tables` |
| Limit rows | `LIMIT n` | `LIMIT n` |
| Parameter style | `%s` | `$1, $2` |
| Schema namespace | `database.table` | `schema.table` |

The abstraction layer normalizes these — callers use `list_resources()`, `query(sql)` without dialect awareness.

### Connection Pooling

- MySQL: `asyncmy.Pool` (connection pool built into driver)
- PostgreSQL: `asyncpg.Pool` (connection pool built into driver)
- Pool min/max configurable per source in `config.toml`
- Health check: `SELECT 1` (MySQL) / `SELECT 1` (PostgreSQL)

### Error Handling

Source-specific errors mapped to common error codes:

| Driver Error | Mapped Code |
|-------------|-------------|
| `OperationalError` | `CONNECTION_FAILED` |
| `ProgrammingError` | `INVALID_SQL` |
| `IntegrityError` | `QUERY_FAILED` |
| Timeout | `QUERY_TIMEOUT` |

### Production Safeguards

Agent-supplied SQL executes against production databases. deepferry is a
read-only query ferry, but "read-only" must be **enforced at multiple layers**,
not assumed:

| Safeguard | MySQL | PostgreSQL | Why |
|-----------|-------|------------|-----|
| **Read-only enforcement** | Connection uses a read-only account; per-query `SET TRANSACTION READ ONLY` | `SET default_transaction_read_only = on` per connection | Defense in depth — never trust that the agent "won't" write |
| **Statement timeout** | `SET SESSION MAX_EXECUTION_TIME = <ms>` per query | `SET statement_timeout = <ms>` per query | Kill runaway queries at the DB, not in Python; value from `QueryRequest.timeout` |
| **Dangerous-keyword block** | Pre-scan statement for `DROP/TRUNCATE/DELETE/UPDATE/INSERT/ALTER/GRANT/REPLACE/MERGE`; reject with `WRITE_BLOCKED` | Same | Blocks accidental writes even if the account somehow has grants |
| **Row cap** | Inject `LIMIT` if absent, capped by `[sources.X].max_rows` (default 100,000) | Same | Prevents multi-million-row fetches from exhausting memory |
| **Streaming cursor** | `cursor.fetchmany(batch)` loop, never `fetchall()` | async `conn.fetchrow()` iteration | Large result sets stream in bounded batches |
| **Pool ceiling** | `maxsize` from `[sources.X].max_pool_size`; default 10 | Same | One source cannot exhaust its own DB connection pool |
| **Per-source concurrency quota** | `asyncio.Semaphore(max_concurrent_queries)` around `execute()` | Same | A busy agent cannot starve other agents or the source DB |

**SQL injection stance** (explicit, documented decision): agents send free-form
SQL — this is a query tool, not a parameterized ORM. Safety comes from the
three gates above (read-only account + dangerous-keyword block + timeout/row
cap), not from forbidding SQL text. This trade-off is recorded so auditors
understand why pass-through SQL is intentional rather than an oversight.

## Acceptance Criteria (M1)

### MySQL
1. Agent executes `SHOW TABLES` → returns table list
2. Agent executes `SELECT * FROM users LIMIT 10` → returns 10 rows
3. Connection to non-existent database returns structured error

### PostgreSQL
4. Agent executes `\dt` equivalent → returns table list
5. Agent executes `SELECT * FROM users LIMIT 10` → returns 10 rows
6. Connection to non-existent database returns structured error

### CI
7. CI includes `mysql:8` and `postgres:16` Docker services
8. Integration tests connect to both databases and verify CRUD
9. A `DROP TABLE` statement is rejected with `WRITE_BLOCKED` before reaching the DB
10. A query exceeding `statement_timeout` is killed server-side and returns `QUERY_TIMEOUT`
11. A `SELECT` returning >`max_rows` rows is truncated; result carries `truncated=true`
12. 100 concurrent queries against one source honor `max_concurrent_queries` (excess queue, do not exceed)
13. `fetchall()` is never called in any code path (grep-enforced: streaming only)

## Dependencies

- [[datasource-abstraction]] — Base class
- [[mcp-server]] — Exposed as tools
- [[duckdb-cross-source]] — SQL sources become ATTACH targets for federation (v1 core)
