from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from typing import TYPE_CHECKING, Any

import duckdb

from deepferry.core.errors import DataSourceError
from deepferry.core.models import ColumnMeta, QueryRequest, StructuredResult

if TYPE_CHECKING:
    from deepferry.config import SourceConfig
    from deepferry.datasources.registry import SourceRegistry

_SOURCE_REF_RE = re.compile(
    r"""
    (?<!['"\w])                    # Not inside a string or identifier
    "?                             # optional opening quote around source_id
    (?P<source>[a-zA-Z_][a-zA-Z0-9_-]*)  # source_id
    "?                             # optional closing quote
    \.                            # dot
    (?P<table>[a-zA-Z_][a-zA-Z0-9_]*)    # table_name
    """,
    re.VERBOSE,
)

_MAX_ROWS_PER_SOURCE = 100_000
_MAX_JOIN_OUTPUT_ROWS = 1_000_000

_SQL_SOURCE_TYPES = frozenset({"mysql", "postgresql"})
_HTTP_SOURCE_TYPES = frozenset({"http", "orchestrated"})


class DuckDBEngine:
    """In-process DuckDB federation engine for cross-source SQL queries.

    Federation strategy (lazy, not ETL):
    1. ATTACH MySQL/PostgreSQL sources using DuckDB scanners
    2. For HTTP sources: fetch response -> CREATE TEMP VIEW from JSON
    3. Agent SQL runs against registered views
    4. All sources share one DuckDB connection -- JOINs are native
    """

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry: SourceRegistry = registry
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._attached: set[str] = set()
        self._temp_files: list[str] = []

    async def connect(self) -> None:
        self._conn = duckdb.connect(":memory:")
        self._conn.execute("INSTALL mysql; LOAD mysql;")
        self._conn.execute("INSTALL postgres; LOAD postgres;")

    async def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            self._attached.clear()
        for path in self._temp_files:
            with contextlib.suppress(OSError):
                os.unlink(path)
        self._temp_files.clear()

    # ── Main entry point ─────────────────────────────────────────────────

    async def execute(
        self,
        query: QueryRequest,
        registry: SourceRegistry,
    ) -> StructuredResult:
        """Execute a cross-source SQL query via DuckDB federation.

        Parameters
        ----------
        query : QueryRequest
            The cross-source query.  ``statement`` uses ``source_id.table_name``
            notation: ``SELECT * FROM mysql_src.customers JOIN http_src.orders``
        registry : SourceRegistry
            Live registry holding connected data sources.

        Returns
        -------
        StructuredResult
        """
        if self._conn is None:
            raise DataSourceError(
                code="ENGINE_NOT_CONNECTED",
                message="DuckDB engine is not connected. Call connect() first.",
            )

        start = time.perf_counter()

        conn = self._conn
        assert conn is not None

        refs = _parse_source_refs(query.statement, registry)

        await self._attach_sql_sources(refs["sql"], registry)
        await self._materialize_http_sources(refs["http"], registry)

        source_breakdown: dict[str, dict[str, Any]] = {}
        for sid in refs["sql"]:
            source_breakdown[sid] = {"type": "sql", "attached": True}
        for sid, tables in refs["http"].items():
            source_breakdown[sid] = {
                "type": "http",
                "materialized": True,
                "tables": sorted(tables),
            }

        transformed = _transform_sql(query.statement, refs, registry)

        self._enforce_limit(transformed, query.max_rows)

        try:
            rel = conn.sql(transformed)
            result = rel.fetchall()
            col_names = rel.columns
            col_types = rel.types
        except Exception as exc:
            raise DataSourceError(
                code="CROSS_QUERY_FAILED",
                message=f"Cross-source query failed: {exc}",
                suggestion="Check the SQL syntax and that all referenced sources are reachable.",
            ) from exc

        elapsed = (time.perf_counter() - start) * 1000

        columns = [
            ColumnMeta(name=name, type=_duckdb_type_name(typ))
            for name, typ in zip(col_names, col_types, strict=True)
        ]
        rows = [dict(zip(col_names, row, strict=True)) for row in result]

        return StructuredResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=round(elapsed, 3),
            source_breakdown=source_breakdown,
        )

    # ── SQL source ATTACH ────────────────────────────────────────────────

    async def _attach_sql_sources(
        self,
        source_ids: set[str],
        registry: SourceRegistry,
    ) -> None:
        conn = self._conn
        assert conn is not None
        for sid in source_ids - self._attached:
            source = registry.get(sid)
            cfg = source._config  # type: ignore[attr-defined]
            attach_str = _build_attach_string(cfg)
            if attach_str:
                db_type = "mysql" if cfg.type == "mysql" else "postgres"
                conn.execute(
                    f"ATTACH '{attach_str}' AS {_quote_ident(sid)} (TYPE {db_type})"
                )
                self._attached.add(sid)

    # ── HTTP source materialization ──────────────────────────────────────

    async def _materialize_http_sources(
        self,
        refs: dict[str, set[str]],
        registry: SourceRegistry,
    ) -> None:
        conn = self._conn
        assert conn is not None
        for source_id, table_names in refs.items():
            source = registry.get(source_id)
            self._create_schema(source_id)
            for table_name in table_names:
                request = QueryRequest(
                    source_id=source_id,
                    statement=table_name,
                    max_rows=_MAX_ROWS_PER_SOURCE,
                )
                result = await source.execute(request)

                if not result.rows:
                    col_selects = ", ".join(
                        f"CAST(NULL AS {_to_duckdb_type(c.type)}) "
                        f"AS {_quote_ident(c.name)}"
                        for c in result.columns
                    )
                    select_body = col_selects or "1 AS _placeholder"
                    conn.execute(
                        f"CREATE OR REPLACE VIEW "
                        f"{_quote_ident(source_id)}.{_quote_ident(table_name)} AS "
                        f"SELECT {select_body} WHERE 1=0"
                    )
                    continue

                fd, path = tempfile.mkstemp(suffix=".json", text=True)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(result.rows, fh, ensure_ascii=False, default=str)
                    path_literal = "'" + path.replace("'", "''") + "'"
                    conn.execute(
                        f"CREATE OR REPLACE VIEW "
                        f"{_quote_ident(source_id)}.{_quote_ident(table_name)} AS "
                        f"SELECT * FROM read_json_auto({path_literal})"
                    )
                    self._temp_files.append(path)
                except Exception:
                    with contextlib.suppress(OSError):
                        os.unlink(path)
                    raise

    # ── Safety ───────────────────────────────────────────────────────────

    def _enforce_limit(self, sql: str, user_max_rows: int | None) -> None:
        upper = sql.upper().strip()
        has_limit = "LIMIT " in upper or "FETCH FIRST" in upper or "FETCH NEXT" in upper
        has_aggregation = any(
            kw in upper
            for kw in ("GROUP BY", "SUM(", "COUNT(", "AVG(", "MIN(", "MAX(")
        )
        if not has_limit and not has_aggregation:
            limit = user_max_rows or _MAX_JOIN_OUTPUT_ROWS
            raise DataSourceError(
                code="MISSING_LIMIT",
                message="Cross-source SELECT without LIMIT or aggregation is rejected.",
                suggestion=f"Add LIMIT {limit} or use GROUP BY / aggregation functions.",
            )

    # ── Internal helpers ─────────────────────────────────────────────────

    def _create_schema(self, name: str) -> None:
        conn = self._conn
        assert conn is not None
        with contextlib.suppress(Exception):
            conn.execute(f"CREATE SCHEMA {_quote_ident(name)}")


# ── Helpers ─────────────────────────────────────────────────────────────


def _parse_source_refs(
    sql: str,
    registry: SourceRegistry,
) -> dict[str, Any]:
    sql_refs: set[str] = set()
    http_refs: dict[str, set[str]] = {}

    for m in _SOURCE_REF_RE.finditer(sql):
        sid = m.group("source")
        table = m.group("table")
        try:
            source = registry.get(sid)
        except Exception:
            continue
        if source.source_type in _SQL_SOURCE_TYPES:
            sql_refs.add(sid)
        elif source.source_type in _HTTP_SOURCE_TYPES:
            http_refs.setdefault(sid, set()).add(table)

    return {"sql": sql_refs, "http": http_refs}


def _transform_sql(sql: str, refs: dict[str, Any], registry: SourceRegistry) -> str:
    for sid in refs["sql"]:
        source = registry.get(sid)
        db_name = source._config.database or "main"  # type: ignore[attr-defined]
        sql = re.sub(
            rf'"?{re.escape(sid)}"?\.',
            rf'"{sid}"."{db_name}".',
            sql,
        )
    return sql


def _build_attach_string(config: SourceConfig) -> str:
    host = config.host or "localhost"
    port = config.port or (3306 if config.type == "mysql" else 5432)
    user = config.user or ""
    password = config.password or ""
    database = config.database or ""

    if config.type == "mysql":
        return (
            f"host={host} port={port} user={user} password={password} "
            f"database={database}"
        )
    if config.type == "postgresql":
        return (
            f"host={host} port={port} user={user} password={password} "
            f"dbname={database}"
        )
    return ""


def _duckdb_type_name(type_obj: Any) -> str:
    name = str(type_obj)
    if name.startswith("VARCHAR"):
        return "VARCHAR"
    return name


def _to_duckdb_type(type_str: str) -> str:
    mapping: dict[str, str] = {
        "INT": "INTEGER",
        "INTEGER": "INTEGER",
        "BIGINT": "BIGINT",
        "SMALLINT": "SMALLINT",
        "TINYINT": "TINYINT",
        "FLOAT": "FLOAT",
        "DOUBLE": "DOUBLE",
        "DECIMAL": "DECIMAL",
        "VARCHAR": "VARCHAR",
        "TEXT": "VARCHAR",
        "CHAR": "VARCHAR",
        "NUMBER": "DOUBLE",
        "BOOLEAN": "BOOLEAN",
        "BOOL": "BOOLEAN",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "JSON": "JSON",
    }
    return mapping.get(type_str.upper(), "VARCHAR")


def _quote_ident(name: str) -> str:
    return f'"{name}"'
