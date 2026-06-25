"""MySQL data source implementation using asyncmy.

Implements the DataSource ABC for MySQL databases with connection pooling,
parameterized queries, schema introspection via information_schema, and
structured error handling.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, ClassVar

import asyncmy
from asyncmy.errors import (
    IntegrityError as _IntegrityError,
)
from asyncmy.errors import (
    OperationalError as _OperationalError,
)
from asyncmy.errors import (
    ProgrammingError as _ProgrammingError,
)

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

# ── MySQL type-code → human-readable name mapping ─────────────────────

_MYSQL_TYPE_MAP: dict[int, str] = {
    0: "DECIMAL",
    1: "TINYINT",
    2: "SMALLINT",
    3: "INT",
    4: "FLOAT",
    5: "DOUBLE",
    6: "NULL",
    7: "TIMESTAMP",
    8: "BIGINT",
    9: "MEDIUMINT",
    10: "DATE",
    11: "TIME",
    12: "DATETIME",
    13: "YEAR",
    15: "VARCHAR",
    16: "BIT",
    245: "JSON",
    246: "DECIMAL",
    247: "ENUM",
    248: "SET",
    249: "TINYBLOB",
    250: "MEDIUMBLOB",
    251: "LONGBLOB",
    252: "BLOB",
    253: "VARCHAR",
    254: "CHAR",
    255: "GEOMETRY",
}


def _map_mysql_type(type_code: int) -> str:
    """Convert a MySQL field-type code to a human-readable string."""
    return _MYSQL_TYPE_MAP.get(type_code, f"UNKNOWN({type_code})")


# ── MySQLDataSource ───────────────────────────────────────────────────


class MySQLDataSource(DataSource):
    """Async MySQL data source backed by an asyncmy connection pool.

    The registry instantiates this class with a ``SourceConfig`` and assigns
    ``source_id`` before calling ``connect()``.

    Lifecycle
    ---------
    1. ``connect()`` → creates an ``asyncmy.Pool`` (idempotent).
    2. ``execute()`` / ``list_resources()`` / ``schema_info()`` / ``health_check()``.
    3. ``disconnect()`` → drains the pool (idempotent).
    """

    source_type: ClassVar[str] = "mysql"

    _READ_ONLY_PREFIXES: ClassVar[tuple[str, ...]] = (
        "SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH",
    )

    def __init__(self, config: SourceConfig) -> None:
        super().__init__()
        self._config = config
        self._pool: asyncmy.Pool | None = None

    @staticmethod
    def _is_read_only(statement: str) -> bool:
        """Check if a SQL statement is a read-only operation."""
        stripped = statement.strip().upper()
        return any(
            stripped.startswith(prefix) for prefix in MySQLDataSource._READ_ONLY_PREFIXES
        )

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the asyncmy connection pool.

        Idempotent — calling ``connect()`` on an already-connected source is
        a no-op.
        """
        if self._pool is not None:
            return
        try:
            self._pool = await asyncmy.create_pool(
                host=self._config.host or "127.0.0.1",
                port=self._config.port or 3306,
                user=self._config.user or "root",
                password=self._config.password or "",
                database=self._config.database or "",
                minsize=2,
                maxsize=10,
            )
        except _OperationalError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=str(exc),
                suggestion=(
                    "Check that the MySQL server is running at "
                    f"{self._config.host}:{self._config.port} and that "
                    "the credentials and database name are correct."
                ),
            ) from exc

    async def disconnect(self) -> None:
        """Close the pool and release all connections.

        Idempotent — safe to call multiple times.
        """
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    # ── Query execution ──────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Execute a SQL statement and return structured results.

        Uses parameterized queries via asyncmy's ``cursor.execute(sql, params)``.
        Columns are derived from ``cursor.description`` and rows are converted
        to dictionaries.

        Parameters
        ----------
        query : QueryRequest
            The query to execute, with optional parameters and timeout.

        Returns
        -------
        StructuredResult
            Column metadata, row data, row count, and execution time.

        Raises
        ------
        DataSourceError
            With code ``CONNECTION_FAILED``, ``QUERY_FAILED``, or ``TIMEOUT``.
        """
        self._require_connected()

        if not self._is_read_only(query.statement):
            raise DataSourceError(
                code="WRITE_NOT_ALLOWED",
                message="Write operations are not permitted through deepferry.",
                suggestion="Use a direct database connection for INSERT/UPDATE/DELETE.",
            )

        params_dict: dict[str, Any] | None = query.params
        params_args: tuple[Any, ...] | None = (
            tuple(params_dict.values()) if params_dict else None
        )
        start = time.perf_counter()

        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cursor:
                    coro = cursor.execute(query.statement, params_args)
                    await asyncio.wait_for(coro, timeout=query.timeout)

                    rows: list[tuple[Any, ...]] = await cursor.fetchall()

                    if cursor.description:
                        col_names = [desc[0] for desc in cursor.description]
                        columns = [
                            ColumnMeta(
                                name=desc[0],
                                type=_map_mysql_type(desc[1]),
                                nullable=bool(desc[6]) if len(desc) > 6 else True,
                            )
                            for desc in cursor.description
                        ]
                    else:
                        col_names = []
                        columns = []

                    dict_rows: list[dict[str, Any]] = [
                        dict(zip(col_names, row)) for row in rows
                    ]

        except TimeoutError:
            raise DataSourceError(
                code="TIMEOUT",
                message=f"Query timed out after {query.timeout}s.",
                suggestion="Increase the timeout or optimise the query.",
            ) from None
        except _ProgrammingError as exc:
            raise DataSourceError(
                code="QUERY_FAILED",
                message=str(exc),
                suggestion="Check your SQL syntax and that referenced tables/columns exist.",
            ) from exc
        except _OperationalError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=str(exc),
                suggestion="The MySQL connection may have been lost. Check if the server is running.",
            ) from exc
        except _IntegrityError as exc:
            raise DataSourceError(
                code="QUERY_FAILED",
                message=str(exc),
                suggestion="The query violated a constraint (duplicate key, foreign key, etc.).",
            ) from exc

        elapsed = (time.perf_counter() - start) * 1000

        if query.max_rows is not None and len(dict_rows) > query.max_rows:
            dict_rows = dict_rows[: query.max_rows]

        return StructuredResult(
            columns=columns,
            rows=dict_rows,
            row_count=len(dict_rows),
            execution_time_ms=round(elapsed, 3),
        )

    # ── Resource discovery ───────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Discover all tables and views in the connected database.

        Queries ``information_schema.TABLES`` scoped to the current database.

        Returns
        -------
        list[Resource]
            One ``Resource`` per table or view.
        """
        self._require_connected()

        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT TABLE_NAME, TABLE_TYPE "
                        "FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA = DATABASE()"
                    )
                    rows = await cursor.fetchall()
                    return [
                        Resource(
                            name=row[0],
                            type="table" if row[1] == "BASE TABLE" else "view",
                        )
                        for row in rows
                    ]
        except _OperationalError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=str(exc),
                suggestion=(
                    "Ensure the MySQL user has SELECT privilege on "
                    "information_schema.TABLES."
                ),
            ) from exc

    # ── Schema introspection ─────────────────────────────────────────

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level metadata for one or all resources.

        Queries ``information_schema.COLUMNS``.  When *resource* is ``None``
        the full schema for the current database is returned.

        Parameters
        ----------
        resource : str | None
            If provided, restrict output to this table/view name.

        Returns
        -------
        Schema
            One ``ResourceMeta`` per resource, each with its column list.
        """
        self._require_connected()

        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cursor:
                    if resource is not None:
                        await cursor.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE "
                            "FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                            (resource,),
                        )
                    else:
                        await cursor.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE "
                            "FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE()"
                        )
                    rows = await cursor.fetchall()

                    # Group columns by table name.
                    tables: dict[str, list[ColumnMeta]] = {}
                    for row in rows:
                        table_name = str(row[0])
                        col_meta = ColumnMeta(
                            name=str(row[1]),
                            type=str(row[2]),
                            nullable=row[3] == "YES",
                        )
                        tables.setdefault(table_name, []).append(col_meta)

                    resources = [
                        ResourceMeta(name=name, columns=cols)
                        for name, cols in tables.items()
                    ]
                    return Schema(resources=resources)

        except _OperationalError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=str(exc),
                suggestion=(
                    "Ensure the MySQL user has SELECT privilege on "
                    "information_schema.COLUMNS."
                ),
            ) from exc

    # ── Health check ─────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Check connectivity with a lightweight ``SELECT 1`` probe.

        Returns
        -------
        HealthStatus
            ``ok=True`` and latency measured when the probe succeeds;
            ``ok=False`` with an error message on failure.
        """
        if self._pool is None:
            return HealthStatus(
                ok=False,
                latency_ms=0,
                error="Not connected — call connect() first.",
            )

        start = time.perf_counter()
        try:
            async with self._pool.acquire() as conn, conn.cursor() as cursor:
                await asyncio.wait_for(cursor.execute("SELECT 1"), timeout=5.0)
            latency = (time.perf_counter() - start) * 1000
            return HealthStatus(ok=True, latency_ms=round(latency, 3))
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000
            return HealthStatus(
                ok=False,
                latency_ms=round(latency, 3),
                error=str(exc),
            )

    # ── Helpers ──────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        """Raise ``DataSourceError`` if the pool has not been created."""
        if self._pool is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="MySQLDataSource is not connected.",
                suggestion="Call connect() before executing any operation.",
            )


# ── Plugin registration ──────────────────────────────────────────────

register_source_type("mysql", MySQLDataSource)
