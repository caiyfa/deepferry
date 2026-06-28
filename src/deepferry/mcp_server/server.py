"""MCP server setup for deepferry using the low-level ``mcp.server.Server`` API.

Provides two transport strategies:

* **stdio** — intended for Claude Desktop and other local MCP clients that
  communicate over stdin/stdout.
* **Streamable HTTP** — intended for remote MCP clients that connect via HTTP
  (uses ``StreamableHTTPSessionManager`` + Starlette).

Both transports share the same ``Server`` instance with identical tool
registration, so agents see the same interface regardless of transport.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import starlette.applications
import starlette.requests
import starlette.responses
import starlette.routing
from starlette.middleware.cors import CORSMiddleware
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from deepferry.core.db import get_db
from deepferry.core.errors import DeepFerryError
from deepferry.core.trace import TraceSink
from deepferry.mcp_server.tools import (
    cross_query,
    end_scenario,
    execute_query,
    list_sources,
    list_tables,
    propose_source,
    schema_info,
    start_scenario,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import aiosqlite

    from deepferry.datasources.registry import SourceRegistry
    from deepferry.engine.duckdb import DuckDBEngine

# ── Tool input schemas ─────────────────────────────────────────────────────

_LIST_SOURCES_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {},
}

_LIST_TABLES_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["source_id"],
    "properties": {
        "source_id": {
            "type": "string",
            "description": "The data source ID from config.toml",
        },
    },
}

_SCHEMA_INFO_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["source_id"],
    "properties": {
        "source_id": {
            "type": "string",
            "description": "The data source ID from config.toml",
        },
        "table": {
            "type": "string",
            "description": "Optional: specific table or view name to introspect",
        },
    },
}

_QUERY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["source_id", "sql"],
    "properties": {
        "source_id": {
            "type": "string",
            "description": "The data source ID from config.toml",
        },
        "sql": {
            "type": "string",
            "description": "SQL statement to execute against the data source",
        },
        "params": {
            "type": "object",
            "description": "Optional query parameters for parameterized queries",
        },
        "max_rows": {
            "type": "integer",
            "description": "Optional maximum number of rows to return",
        },
    },
}

_START_SCENARIO_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "description": "Optional human-readable label for the scenario",
        },
    },
}

_END_SCENARIO_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["scenario_id"],
    "properties": {
        "scenario_id": {
            "type": "string",
            "description": "The scenario UUID to close",
        },
    },
}

_CROSS_QUERY_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["sql"],
    "properties": {
        "sql": {
            "type": "string",
            "description": (
                "Cross-source SQL with source_id.table_name references, e.g. "
                "\"SELECT c.name, o.amount FROM mysql_src.customers c "
                "JOIN http_src.orders o ON c.id = o.user_id\""
            ),
        },
        "max_rows": {
            "type": "integer",
            "description": "Optional maximum number of rows to return",
        },
    },
}

_PROPOSE_SOURCE_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["type", "host"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["mysql", "postgresql", "http"],
            "description": "数据源类型 (data source type)",
        },
        "host": {
            "type": "string",
            "description": "Hostname or IP of the target service",
        },
        "port": {
            "type": "integer",
            "description": "Optional TCP port",
        },
        "database": {
            "type": "string",
            "description": "Optional database name (for SQL sources)",
        },
        "name": {
            "type": "string",
            "description": "可读名称 (human-readable label)",
        },
    },
}

ALL_TOOLS: list[types.Tool] = [
    types.Tool(
        name="list_sources",
        description="List all configured data sources with their type and health status.",
        inputSchema=_LIST_SOURCES_SCHEMA,
    ),
    types.Tool(
        name="list_tables",
        description="List all tables and views available in a data source.",
        inputSchema=_LIST_TABLES_SCHEMA,
    ),
    types.Tool(
        name="schema_info",
        description="Get column-level schema metadata for tables in a data source.",
        inputSchema=_SCHEMA_INFO_SCHEMA,
    ),
    types.Tool(
        name="query",
        description="Execute a SQL query against a data source and return structured results.",
        inputSchema=_QUERY_SCHEMA,
    ),
    types.Tool(
        name="start_scenario",
        description="Open a named investigation scenario. Returns a scenario_id for grouping subsequent queries.",
        inputSchema=_START_SCENARIO_SCHEMA,
    ),
    types.Tool(
        name="end_scenario",
        description="Close a scenario. The scenario becomes read-only in the trace store.",
        inputSchema=_END_SCENARIO_SCHEMA,
    ),
    types.Tool(
        name="cross_query",
        description=(
            "Execute a SQL query that JOINs/UNIONs data across multiple configured "
            "sources. Reference sources as source_id.table_name "
            "(e.g., mysql_src.customers JOIN http_src.orders)."
        ),
        inputSchema=_CROSS_QUERY_SCHEMA,
    ),
    types.Tool(
        name="propose_source",
        description=(
            "Propose a new data source for human review in the config panel "
            "(desktop mode only). Returns a provisional source_id with status "
            "'pending'; the source is not activated until the user approves it."
        ),
        inputSchema=_PROPOSE_SOURCE_SCHEMA,
    ),
]

# Tools only exposed when the server runs in desktop mode (i.e. a human is
# present to approve proposals via the config panel).  Remote/server mode
# hides them so untrusted agents cannot enqueue config changes.
_DESKTOP_ONLY_TOOLS: frozenset[str] = frozenset({"propose_source"})

# ── Server factory ─────────────────────────────────────────────────────────


def create_server(
    registry: SourceRegistry,
    engine: DuckDBEngine | None = None,
    desktop_mode: bool = False,
    trace_sink: TraceSink | None = None,
    session_id: str | None = None,
) -> Server[Any, Any]:
    """Build and return a configured ``mcp.server.Server`` instance.

    Registers deepferry tools (list_sources, list_tables, schema_info,
    query, start_scenario, end_scenario, cross_query, propose_source) with
    their input schemas and async handlers.  All errors returned to agents are
    structured JSON — no Python tracebacks leak through the tool boundary.

    Parameters
    ----------
    registry : SourceRegistry
        The live registry holding connected data sources.
    engine : DuckDBEngine | None
        Optional DuckDB federation engine.  Required for the ``cross_query``
        tool to function; when ``None``, ``cross_query`` returns a structured
        ``ENGINE_UNAVAILABLE`` error.
    desktop_mode : bool
        When ``True``, desktop-only tools (currently ``propose_source``) are
        advertised to the client.  When ``False`` (the default, appropriate
        for unattended server deployments), those tools are hidden from the
        tool list and their handlers return ``TOOL_NOT_AVAILABLE``.
    trace_sink : TraceSink | None
        Optional trace sink.  When provided, ``query`` / ``cross_query``
        executions are recorded and ``start_scenario`` / ``end_scenario``
        persist to the ``query_scenarios`` table.  When ``None`` (default),
        all tracing is no-op and scenarios are in-memory UUIDs only.
    session_id : str | None
        MCP session ID used to group scenarios and executions.  For the HTTP
        transport a process-level UUID is generated per server start; for
        stdio the literal ``"stdio"`` is used.  Passed through to the tool
        handlers alongside *trace_sink*.

    Returns
    -------
    Server[Any, Any]
        The configured MCP server instance.
    """

    app: Server[Any, Any] = Server("deepferry")

    @app.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def handle_list_tools() -> list[types.Tool]:
        if desktop_mode:
            return list(ALL_TOOLS)
        return [t for t in ALL_TOOLS if t.name not in _DESKTOP_ONLY_TOOLS]

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            # Guard desktop-only tools when not in desktop mode.
            if name in _DESKTOP_ONLY_TOOLS and not desktop_mode:
                return [
                    types.TextContent(
                        type="text",
                        text=json.dumps({
                            "code": "TOOL_NOT_AVAILABLE",
                            "message": (
                                f"Tool {name!r} is only available in desktop mode."
                            ),
                            "suggestion": (
                                "Start deepferry with desktop mode enabled to "
                                "use this tool."
                            ),
                        }),
                    )
                ]

            if name == "list_sources":
                sources = await list_sources(registry)
                payload = [s.model_dump(mode="json") for s in sources]
                return [types.TextContent(type="text", text=json.dumps(payload))]

            if name == "list_tables":
                tables = await list_tables(registry, arguments["source_id"])
                payload = [t.model_dump(mode="json") for t in tables]
                return [types.TextContent(type="text", text=json.dumps(payload))]

            if name == "schema_info":
                table: str | None = arguments.get("table")
                schema = await schema_info(registry, arguments["source_id"], table)
                return [types.TextContent(type="text", text=schema.model_dump_json())]

            if name == "query":
                result = await execute_query(
                    registry,
                    source_id=arguments["source_id"],
                    sql=arguments["sql"],
                    params=arguments.get("params"),
                    max_rows=arguments.get("max_rows"),
                    trace_sink=trace_sink,
                    session_id=session_id,
                    scenario_id=arguments.get("scenario_id"),
                )
                return [types.TextContent(type="text", text=result.model_dump_json())]

            if name == "start_scenario":
                label: str | None = arguments.get("label")
                scenario_result = await start_scenario(
                    label,
                    trace_sink=trace_sink,
                    session_id=session_id,
                )
                return [types.TextContent(type="text", text=json.dumps(scenario_result))]

            if name == "cross_query":
                if engine is None:
                    return [
                        types.TextContent(
                            type="text",
                            text=json.dumps({
                                "code": "ENGINE_UNAVAILABLE",
                                "message": "DuckDB engine is not initialized.",
                                "suggestion": "Ensure the engine is passed to create_server().",
                            }),
                        )
                    ]
                result = await cross_query(
                    registry,
                    engine,
                    sql=arguments["sql"],
                    max_rows=arguments.get("max_rows"),
                    trace_sink=trace_sink,
                    session_id=session_id,
                    scenario_id=arguments.get("scenario_id"),
                )
                return [types.TextContent(type="text", text=result.model_dump_json())]

            if name == "end_scenario":
                end_result = await end_scenario(
                    arguments["scenario_id"],
                    trace_sink=trace_sink,
                )
                return [types.TextContent(type="text", text=json.dumps(end_result))]

            if name == "propose_source":
                proposal = await propose_source(
                    source_type=arguments["type"],
                    host=arguments["host"],
                    port=arguments.get("port"),
                    database=arguments.get("database"),
                    name=arguments.get("name"),
                )
                return [types.TextContent(type="text", text=json.dumps(proposal))]

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "code": "UNKNOWN_TOOL",
                        "message": f"No handler registered for tool {name!r}",
                        "suggestion": (
                            "Available tools: list_sources, list_tables, "
                            "schema_info, query, start_scenario, end_scenario, "
                            "cross_query, propose_source"
                        ),
                    }),
                )
            ]

        except DeepFerryError as exc:
            return [types.TextContent(type="text", text=json.dumps(exc.to_dict()))]

        except Exception as exc:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "code": "INTERNAL_ERROR",
                        "message": str(exc),
                    }),
                )
            ]

    return app


# ── Transport runners ──────────────────────────────────────────────────────


async def run_stdio_server(
    registry: SourceRegistry,
    engine: DuckDBEngine | None = None,
    desktop_mode: bool = False,
) -> None:
    """Start the MCP server over stdin/stdout (for Claude Desktop etc.).

    Blocks until the stdio streams are closed.

    Parameters
    ----------
    registry : SourceRegistry
        The live registry holding connected data sources.
    engine : DuckDBEngine | None
        Optional DuckDB federation engine for ``cross_query``.
    desktop_mode : bool
        When ``True``, expose desktop-only tools (e.g. ``propose_source``).
        Typically ``True`` for the stdio transport since it is used by local
        desktop clients.

    Notes
    -----
    The stdio transport does not persist traces (no ``db_path``); scenarios
    and executions stay in-memory.  A synthetic ``"stdio"`` session_id is
    used so any future trace_sink wiring groups rows correctly.
    """
    app = create_server(
        registry,
        engine=engine,
        desktop_mode=desktop_mode,
        session_id="stdio",
    )
    async with stdio_server() as streams:
        await app.run(
            streams[0],
            streams[1],
            app.create_initialization_options(),
        )


async def run_http_server(
    registry: SourceRegistry,
    host: str = "127.0.0.1",
    port: int = 8000,
    engine: DuckDBEngine | None = None,
    desktop_mode: bool = False,
    db_path: str | None = None,
    trace_sink: TraceSink | None = None,
    config_path: Path | None = None,
) -> None:
    """Start the MCP server over Streamable HTTP.

    Parameters
    ----------
    registry : SourceRegistry
        The live registry holding connected data sources.
    host : str
        Bind address (default ``127.0.0.1``).
    port : int
        Bind port (default ``8000``).
    engine : DuckDBEngine | None
        Optional DuckDB federation engine for ``cross_query``.
    desktop_mode : bool
        When ``True``, expose desktop-only tools (e.g. ``propose_source``).
        Defaults to ``False`` — remote HTTP deployments are typically
        unattended.
    db_path : str | None
        Optional SQLite database path.  When provided, a connection is opened
        and injected into the config-panel app for query-history persistence.
    trace_sink : TraceSink | None
        Optional trace sink injected into the config-panel app for execution
        tracing.
    config_path : Path | None
        Optional path to ``config.toml`` on disk.  When provided, the config
        CRUD endpoints (``POST/PUT/DELETE /api/config/sources`` and
        ``POST /api/config/reload``) become functional and persist mutations
        back to this file.
    """
    import uvicorn

    db: aiosqlite.Connection | None = None
    if db_path is not None:
        db = await get_db(db_path)

    # Auto-provision a TraceSink from the SQLite connection when the caller
    # supplied a db_path but no pre-built sink.  The schema is idempotent so
    # calling init_schema here is safe even if the panel already ran it.
    if trace_sink is None and db is not None:
        trace_sink = TraceSink(db)
        await TraceSink.init_schema(db)

    # Process-level session ID.  The MCP protocol layer wraps the server in
    # StreamableHTTPSessionManager which makes per-request header extraction
    # complex; a stable UUID per server start is sufficient for grouping
    # scenarios and executions in the audit store.
    _session_id = str(uuid.uuid4())

    server = create_server(
        registry,
        engine=engine,
        desktop_mode=desktop_mode,
        trace_sink=trace_sink,
        session_id=_session_id,
    )
    session_manager = StreamableHTTPSessionManager(app=server)

    async def lifespan(app: starlette.applications.Starlette) -> AsyncIterator[None]:  # noqa: ARG001
        async with session_manager.run():
            yield

    async def health(request: starlette.requests.Request) -> starlette.responses.JSONResponse:  # noqa: ARG001
        return starlette.responses.JSONResponse({"status": "ok"})

    from deepferry.web.app import init_app

    config_app = init_app(
        registry, db=db, trace_sink=trace_sink, config_path=config_path
    )

    starlette_app = starlette.applications.Starlette(
        lifespan=lifespan,  # type: ignore[arg-type]
        routes=[
            starlette.routing.Route("/health", health, methods=["GET"]),
            starlette.routing.Mount("/api", app=config_app),
            starlette.routing.Mount("/", app=session_manager.handle_request),
        ],
    )

    # Allow desktop client (Vite dev server) cross-origin requests.
    starlette_app = CORSMiddleware(  # type: ignore[assignment]
        starlette_app,
        allow_origins=["http://localhost:5173", "tauri://localhost"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()
