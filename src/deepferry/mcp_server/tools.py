"""Tool handler functions for the deepferry MCP server.

Each function performs a single MCP tool operation (list sources, list tables,
schema introspection, query execution).  They delegate to the SourceRegistry and
DataSource implementations, catching unexpected exceptions and wrapping them in
structured ``DataSourceError`` instances so agents never see raw tracebacks.

DeepFerryError subclasses are re-raised as-is; the call_tool handler in
``server.py`` formats them into JSON for the agent.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from deepferry.core.errors import DataSourceError, DeepFerryError
from deepferry.core.models import QueryRequest, QueryResult, TableInfo

if TYPE_CHECKING:
    from deepferry.core.models import Schema, SourceSummary
    from deepferry.core.trace import TraceSink
    from deepferry.datasources.registry import SourceRegistry
    from deepferry.engine.duckdb import DuckDBEngine


async def list_sources(registry: SourceRegistry) -> list[SourceSummary]:
    """Return all configured data sources with their type and health status.

    Delegates to ``registry.list_sources()`` which returns a ``SourceSummary``
    per source.  Health is reported as ``"unknown"`` for sources that have not
    been explicitly probed.
    """
    try:
        return registry.list_sources()
    except DeepFerryError:
        raise
    except Exception as exc:
        raise DataSourceError(
            code="LIST_SOURCES_FAILED",
            message=f"Failed to enumerate data sources: {exc}",
        ) from exc


async def list_tables(
    registry: SourceRegistry,
    source_id: str,
) -> list[TableInfo]:
    """List all tables and views available in *source_id*.

    Calls ``source.list_resources()`` and converts each ``Resource`` into a
    ``TableInfo`` model suitable for returning to an LLM agent.
    """
    try:
        source = registry.get(source_id)
        resources = await source.list_resources()
        return [
            TableInfo(
                name=r.name,
                type=r.type,
                row_count=None,  # Not available from resource listing alone
            )
            for r in resources
        ]
    except DeepFerryError:
        raise
    except Exception as exc:
        raise DataSourceError(
            code="LIST_TABLES_FAILED",
            message=f"Failed to list tables for source {source_id!r}: {exc}",
        ) from exc


async def schema_info(
    registry: SourceRegistry,
    source_id: str,
    table: str | None = None,
) -> Schema:
    """Get column-level schema metadata for *source_id*.

    When *table* is ``None`` the source returns schema for all known resources.
    Otherwise metadata is limited to the named resource.
    """
    try:
        source = registry.get(source_id)
        return await source.schema_info(resource=table)
    except DeepFerryError:
        raise
    except Exception as exc:
        raise DataSourceError(
            code="SCHEMA_INFO_FAILED",
            message=f"Failed to retrieve schema for source {source_id!r}: {exc}",
        ) from exc


async def execute_query(
    registry: SourceRegistry,
    source_id: str,
    sql: str,
    params: dict[str, Any] | None = None,
    max_rows: int | None = None,
    trace_sink: TraceSink | None = None,
    session_id: str | None = None,
    scenario_id: str | None = None,
) -> QueryResult:
    """Execute a SQL query against *source_id* and return structured results.

    Parameters
    ----------
    registry : SourceRegistry
        The live registry holding connected data sources.
    source_id : str
        The data source ID declared in ``config.toml``.
    sql : str
        The SQL statement to execute (parameterized via *params*).
    params : dict[str, Any] | None
        Optional named parameters for the query.
    max_rows : int | None
        Optional limit on the number of rows returned.
    trace_sink : TraceSink | None
        Optional trace sink.  When provided, a fire-and-forget execution
        record is opened and closed around the query, linked to *scenario_id*
        and *session_id*.  Trace failures never propagate to the caller.
    session_id : str | None
        MCP session ID captured at the transport layer; recorded on the
        execution when *trace_sink* is provided.
    scenario_id : str | None
        Active scenario UUID; attaches the execution to the scenario when
        *trace_sink* is provided.

    Returns
    -------
    QueryResult
        Columns, rows, row count, execution time, and the source ID.
    """
    try:
        source = registry.get(source_id)

        request = QueryRequest(
            source_id=source_id,
            statement=sql,
            params=params,
            max_rows=max_rows,
        )

        result = await source.execute(request)

        query_result = QueryResult(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            execution_time_ms=result.execution_time_ms,
            source_id=source_id,
        )

        # Fire-and-forget trace recording — never blocks the response path
        # and never surfaces trace failures to the agent.
        if trace_sink is not None:
            try:
                execution = await trace_sink.start_execution(
                    source_id,
                    scenario_id=scenario_id,
                    session_id=session_id,
                )
                await trace_sink.finish_execution(execution)
            except Exception:
                pass  # noqa: BLE001 - tracing is best-effort

        return query_result
    except DeepFerryError:
        raise
    except Exception as exc:
        raise DataSourceError(
            code="QUERY_FAILED",
            message=f"Query failed on source {source_id!r}: {exc}",
        ) from exc


async def start_scenario(
    label: str | None = None,
    trace_sink: TraceSink | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Open a named investigation scenario. Returns scenario_id.

    When *trace_sink* (and *session_id*) are provided the scenario is
    persisted to the ``query_scenarios`` table; otherwise a UUID is
    generated in-memory only (no persistence).

    Parameters
    ----------
    label : str | None
        Optional human-readable label for the scenario.
    trace_sink : TraceSink | None
        Optional trace sink for persistence.
    session_id : str | None
        Required when *trace_sink* is provided — the MCP session to
        associate the scenario with.

    Returns
    -------
    dict
        ``{"scenario_id": str, "label": str | None}``
    """
    if trace_sink is not None and session_id is not None:
        scenario_id = await trace_sink.start_scenario(session_id, label)
    else:
        scenario_id = str(uuid.uuid4())
    return {"scenario_id": scenario_id, "label": label}


async def cross_query(
    registry: SourceRegistry,
    engine: DuckDBEngine,
    sql: str,
    max_rows: int | None = None,
    trace_sink: TraceSink | None = None,
    session_id: str | None = None,
    scenario_id: str | None = None,
) -> QueryResult:
    """Execute a cross-source SQL query via DuckDB federation.

    Reference sources as ``source_id.table_name`` (e.g.
    ``mysql_src.customers JOIN http_src.orders``) in the SQL statement.
    DuckDB handles JOINs, UNIONs, and aggregations across heterogeneous
    sources in-process.

    Parameters
    ----------
    registry : SourceRegistry
        The live registry holding connected data sources.
    engine : DuckDBEngine
        The DuckDB federation engine (must be connected).
    sql : str
        Cross-source SQL with ``source_id.table_name`` references.
    max_rows : int | None
        Optional limit on the number of rows returned.
    trace_sink : TraceSink | None
        Optional trace sink.  When provided, a fire-and-forget execution
        record is opened and closed around the cross-query.
    session_id : str | None
        MCP session ID recorded on the execution when *trace_sink* is set.
    scenario_id : str | None
        Active scenario UUID to attach the execution to.

    Returns
    -------
    QueryResult
    """
    try:
        request = QueryRequest(
            source_id="__cross__",
            statement=sql,
            max_rows=max_rows,
        )
        result = await engine.execute(request, registry)
        query_result = QueryResult(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            execution_time_ms=result.execution_time_ms,
            source_id="__cross__",
        )

        # Fire-and-forget trace recording.
        if trace_sink is not None:
            try:
                execution = await trace_sink.start_execution(
                    "__cross__",
                    scenario_id=scenario_id,
                    session_id=session_id,
                )
                await trace_sink.finish_execution(execution)
            except Exception:
                pass  # noqa: BLE001 - tracing is best-effort

        return query_result
    except DeepFerryError:
        raise
    except Exception as exc:
        raise DataSourceError(
            code="CROSS_QUERY_FAILED",
            message=f"Cross-source query failed: {exc}",
        ) from exc


async def end_scenario(
    scenario_id: str,
    trace_sink: TraceSink | None = None,
) -> dict[str, Any]:
    """Close a scenario. Returns summary.

    When *trace_sink* is provided, marks the scenario as ``closed`` in
    the trace store (sets ``finished_at``).

    Parameters
    ----------
    scenario_id : str
        The scenario UUID to close.
    trace_sink : TraceSink | None
        Optional trace sink for persistence.

    Returns
    -------
    dict
        ``{"scenario_id": str, "status": str}``
    """
    if trace_sink is not None:
        await trace_sink.end_scenario(scenario_id)
    return {"scenario_id": scenario_id, "status": "closed"}


async def propose_source(
    source_type: str,
    host: str,
    port: int | None = None,
    database: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Propose a new data source for human review in the config panel.

    This is a **desktop-mode** affordance: the agent suggests a source
    configuration but does NOT modify ``config.toml`` or instantiate anything.
    The returned ``source_id`` is provisional (``proposed-<short-uuid>``) and
    becomes live only after the user approves it in the config panel and
    triggers a registry refresh.

    Parameters
    ----------
    source_type : str
        The data source type (``"mysql"``, ``"postgresql"``, ``"http"``).
    host : str
        Hostname or IP of the target service.
    port : int | None
        Optional TCP port.
    database : str | None
        Optional database name (for SQL sources).
    name : str | None
        Optional human-readable label.

    Returns
    -------
    dict
        ``{"source_id": str, "status": "pending", "type": str, ...}``
    """
    source_id = f"proposed-{uuid.uuid4().hex[:8]}"
    return {
        "source_id": source_id,
        "status": "pending",
        "type": source_type,
        "host": host,
        "port": port,
        "database": database,
        "name": name,
        "message": "Source proposed. Use the config panel to review and activate.",
    }
