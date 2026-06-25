"""PostgreSQL data source implementation using asyncpg.

Provides PostgreSQLDataSource — a concrete DataSource that communicates with
PostgreSQL via ``asyncpg``.  Supports connection pooling, parameterized queries
with ``$1`` / ``$2`` positional parameter syntax, schema introspection via
``pg_catalog`` and ``information_schema``, and graceful health checks.

Registered with the registry so that ``type = "postgresql"`` in config.toml
resolves to this class.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

import asyncpg

from deepferry.core.errors import DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    HealthStatus,
    QueryRequest,
    Resource,
    ResourceMeta,
    Schema,
    StructuredResult,
)
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import register_source_type

if TYPE_CHECKING:
    from deepferry.config import SourceConfig

# ── DataSource implementation ───────────────────────────────────────────────


class PostgreSQLDataSource(DataSource):
    """PostgreSQL data source backed by an ``asyncpg`` connection pool.

    Implements all six abstract methods of :class:`DataSource`.  Uses
    ``asyncpg.create_pool`` with ``min_size=2, max_size=10`` to balance
    concurrency with resource usage.

    Parameters
    ----------
    config : SourceConfig
        Source configuration from config.toml (host, port, credentials, database).
    """

    source_type: ClassVar[str] = "postgresql"

    _READ_ONLY_PREFIXES: ClassVar[tuple[str, ...]] = (
        "SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH",
    )

    def __init__(self, config: SourceConfig) -> None:
        super().__init__()
        self._config = config
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    @staticmethod
    def _is_read_only(statement: str) -> bool:
        """Check if a SQL statement is a read-only operation."""
        stripped = statement.strip().upper()
        return any(
            stripped.startswith(prefix)
            for prefix in PostgreSQLDataSource._READ_ONLY_PREFIXES
        )

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the asyncpg connection pool (idempotent)."""
        if self._pool is not None:
            return
        try:
            self._pool = await asyncpg.create_pool(
                host=self._config.host or "127.0.0.1",
                port=self._config.port or 5432,
                user=self._config.user or "postgres",
                password=self._config.password or "",
                database=self._config.database or "postgres",
                min_size=2,
                max_size=10,
            )
        except Exception as exc:
            self._pool = None
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=f"Failed to connect to PostgreSQL at "
                f"{self._config.host}:{self._config.port}/{self._config.database}: "
                f"{exc}",
                suggestion="Check host, port, credentials, and that the database "
                "exists and accepts connections.",
            ) from exc

    async def disconnect(self) -> None:
        """Close the asyncpg connection pool (idempotent)."""
        if self._pool is not None:
            pool = self._pool
            self._pool = None
            try:
                await pool.close()
            except Exception as exc:
                raise DataSourceError(
                    code="DISCONNECT_FAILED",
                    message=f"Failed to close PostgreSQL pool: {exc}",
                    suggestion="Connections may have already been terminated.",
                ) from exc

    # ── Query execution ─────────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Execute a SQL statement and return structured results.

        Converts ``query.params`` (a ``dict[str, Any]``) into a positional
        list so that ``$1``, ``$2``, … placeholders in the statement map to
        the corresponding dict values in insertion order.
        """
        if self._pool is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="PostgreSQL datasource is not connected.",
                suggestion="Call connect() before executing queries.",
            )

        if not self._is_read_only(query.statement):
            raise DataSourceError(
                code="WRITE_NOT_ALLOWED",
                message="Write operations are not permitted through deepferry.",
                suggestion="Use a direct database connection for INSERT/UPDATE/DELETE.",
            )

        params_list: list[Any] = []
        if query.params:
            params_list = list(query.params.values())

        start = time.perf_counter()
        try:
            async with self._pool.acquire() as conn:
                rows: list[asyncpg.Record] = await conn.fetch(
                    query.statement, *params_list, timeout=query.timeout
                )
        except (TimeoutError, asyncpg.exceptions.QueryCanceledError) as exc:
            raise DataSourceError(
                code="TIMEOUT",
                message=f"Query timed out after {query.timeout}s",
                suggestion="Increase the query timeout or optimise the statement.",
            ) from exc
        except (
            asyncpg.exceptions.ConnectionDoesNotExistError,
            asyncpg.exceptions.ConnectionFailureError,
            asyncpg.exceptions.CannotConnectNowError,
            asyncpg.exceptions.TooManyConnectionsError,
            OSError,
        ) as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=f"PostgreSQL connection lost: {exc}",
                suggestion="Verify the database is running and reconnect.",
            ) from exc
        except (
            asyncpg.exceptions.PostgresSyntaxError,
            asyncpg.exceptions.UndefinedTableError,
            asyncpg.exceptions.UndefinedColumnError,
            asyncpg.exceptions.InvalidSchemaNameError,
        ) as exc:
            raise DataSourceError(
                code="QUERY_FAILED",
                message=str(exc),
                suggestion="Check the SQL syntax, table names, and column references.",
            ) from exc
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            raise DataSourceError(
                code="QUERY_FAILED",
                message=f"Unexpected PostgreSQL error: {exc}",
                suggestion="Review the query and data source configuration.",
            ) from exc

        elapsed = (time.perf_counter() - start) * 1000

        # Build column metadata from the first row's keys
        columns: list[ColumnMeta] = []
        if rows:
            col_names = list(rows[0].keys())
            for name in col_names:
                columns.append(
                    ColumnMeta(name=name, type="unknown", nullable=True)
                )
        else:
            # No rows returned — attempt to infer columns by preparing the statement
            try:
                async with self._pool.acquire() as conn:
                    stmt = await conn.prepare(query.statement)
                    attrs = stmt.get_attributes()
                    for attr in attrs:
                        pg_type = self._oid_to_typename(attr.type_oid, conn)
                        columns.append(
                            ColumnMeta(
                                name=attr.name,
                                type=pg_type,
                                nullable=True,
                            )
                        )
            except Exception:
                # If prepare fails (e.g., DDL), leave columns empty
                pass

        # Convert asyncpg.Record objects to plain dicts
        result_rows: list[dict[str, Any]] = []
        for row in rows:
            result_rows.append(dict(row))

        # Apply max_rows truncation
        if query.max_rows is not None and len(result_rows) > query.max_rows:
            result_rows = result_rows[: query.max_rows]

        return StructuredResult(
            columns=columns,
            rows=result_rows,
            row_count=len(result_rows),
            execution_time_ms=round(elapsed, 2),
        )

    # ── Resource discovery ──────────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Return all user tables and views in the ``public`` schema."""
        if self._pool is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="PostgreSQL datasource is not connected.",
                suggestion="Call connect() before listing resources.",
            )

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT tablename, "
                    "(SELECT 'view' FROM pg_catalog.pg_views "
                    " WHERE schemaname = 'public' AND viewname = t.tablename) "
                    "AS resource_type "
                    "FROM pg_catalog.pg_tables t "
                    "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY tablename"
                )
        except Exception as exc:
            raise DataSourceError(
                code="QUERY_FAILED",
                message=f"Failed to list PostgreSQL resources: {exc}",
                suggestion="Check connection and permissions on pg_catalog.",
            ) from exc

        resources: list[Resource] = []
        for row in rows:
            rtype = row["resource_type"] if row["resource_type"] else "table"
            resources.append(Resource(name=row["tablename"], type=rtype))
        return resources

    # ── Schema introspection ────────────────────────────────────────────

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level metadata from ``information_schema.columns``.

        When *resource* is ``None``, returns schema for all tables in the
        ``public`` schema.  Otherwise returns schema for only the named table.
        """
        if self._pool is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="PostgreSQL datasource is not connected.",
                suggestion="Call connect() before introspecting schema.",
            )

        base_query = (
            "SELECT table_name, column_name, data_type, "
            "CASE WHEN is_nullable = 'YES' THEN TRUE ELSE FALSE END AS nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        params: list[Any] = []
        if resource is not None:
            base_query += " AND table_name = $1"
            params.append(resource)
        base_query += " ORDER BY table_name, ordinal_position"

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(base_query, *params)
        except Exception as exc:
            raise DataSourceError(
                code="QUERY_FAILED",
                message=f"Failed to introspect PostgreSQL schema: {exc}",
                suggestion="Check connection and permissions on information_schema.",
            ) from exc

        # Group columns by table_name
        table_columns: dict[str, list[ColumnMeta]] = {}
        for row in rows:
            table_name: str = row["table_name"]
            if table_name not in table_columns:
                table_columns[table_name] = []
            table_columns[table_name].append(
                ColumnMeta(
                    name=row["column_name"],
                    type=row["data_type"],
                    nullable=bool(row["nullable"]),
                )
            )

        resources_out: list[ResourceMeta] = []
        for table_name, cols in table_columns.items():
            resources_out.append(ResourceMeta(name=table_name, columns=cols))

        return Schema(resources=resources_out)

    # ── Health check ────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Probe connectivity with ``SELECT 1`` and measure latency."""
        if self._pool is None:
            return HealthStatus(
                ok=False,
                latency_ms=0,
                error="Not connected — call connect() first.",
            )

        try:
            start = time.perf_counter()
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1", timeout=5)
            elapsed = (time.perf_counter() - start) * 1000
            return HealthStatus(ok=True, latency_ms=round(elapsed, 2))
        except Exception as exc:
            return HealthStatus(
                ok=False,
                latency_ms=0,
                error=f"Health check failed: {exc}",
            )

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _oid_to_typename(oid: int, conn: asyncpg.Connection[asyncpg.Record]) -> str:
        """Map a PostgreSQL type OID to a human-readable type name.

        Uses the connection's type cache for fast lookups.  Falls back to
        ``"unknown"`` when the OID is not recognised.
        """
        try:
            # asyncpg internal: conn._server_caps.type_by_oid
            pg_type = conn.get_types().get(oid)
            if pg_type is not None:
                return str(pg_type)
        except Exception:
            pass
        return "unknown"


# ── Auto-registration ───────────────────────────────────────────────────────

register_source_type("postgresql", PostgreSQLDataSource)
