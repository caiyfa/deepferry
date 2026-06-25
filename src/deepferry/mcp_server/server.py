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
from typing import TYPE_CHECKING, Any

import starlette.applications
import starlette.requests
import starlette.responses
import starlette.routing
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from deepferry.core.errors import DeepFerryError
from deepferry.mcp_server.tools import (
    cross_query,
    end_scenario,
    execute_query,
    list_sources,
    list_tables,
    schema_info,
    start_scenario,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
]

# ── Server factory ─────────────────────────────────────────────────────────


def create_server(
    registry: SourceRegistry,
    engine: DuckDBEngine | None = None,
) -> Server[Any, Any]:
    """Build and return a configured ``mcp.server.Server`` instance.

    Registers deepferry tools (list_sources, list_tables, schema_info,
    query, start_scenario, end_scenario, cross_query) with their input
    schemas and async handlers.  All errors returned to agents are
    structured JSON — no Python tracebacks leak through the tool boundary.
    """

    app: Server[Any, Any] = Server("deepferry")

    @app.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def handle_list_tools() -> list[types.Tool]:
        return ALL_TOOLS

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
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
                )
                return [types.TextContent(type="text", text=result.model_dump_json())]

            if name == "start_scenario":
                label: str | None = arguments.get("label")
                scenario_result = await start_scenario(label)
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
                )
                return [types.TextContent(type="text", text=result.model_dump_json())]

            if name == "end_scenario":
                end_result = await end_scenario(arguments["scenario_id"])
                return [types.TextContent(type="text", text=json.dumps(end_result))]

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "code": "UNKNOWN_TOOL",
                        "message": f"No handler registered for tool {name!r}",
                        "suggestion": (
                            "Available tools: list_sources, list_tables, "
                            "schema_info, query, start_scenario, end_scenario, "
                            "cross_query"
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
) -> None:
    """Start the MCP server over stdin/stdout (for Claude Desktop etc.).

    Blocks until the stdio streams are closed.
    """
    app = create_server(registry, engine=engine)
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
) -> None:
    """Start the MCP server over Streamable HTTP."""
    import uvicorn

    server = create_server(registry, engine=engine)
    session_manager = StreamableHTTPSessionManager(app=server)

    async def lifespan(app: starlette.applications.Starlette) -> AsyncIterator[None]:  # noqa: ARG001
        async with session_manager.run():
            yield

    async def health(request: starlette.requests.Request) -> starlette.responses.JSONResponse:  # noqa: ARG001
        return starlette.responses.JSONResponse({"status": "ok"})

    starlette_app = starlette.applications.Starlette(
        lifespan=lifespan,  # type: ignore[arg-type]
        routes=[
            starlette.routing.Route("/health", health, methods=["GET"]),
            starlette.routing.Mount("/", app=session_manager.handle_request),
        ],
    )

    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()
