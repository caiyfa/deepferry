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

## Dependencies

- [[datasource-abstraction]] — Base class
- [[mcp-server]] — Exposed as tools
