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
import starlette.routing
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from deepferry.core.errors import DeepFerryError
from deepferry.mcp_server.tools import execute_query, list_sources, list_tables, schema_info

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.types import Receive, Scope, Send

    from deepferry.datasources.registry import SourceRegistry

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
]

# ── Server factory ─────────────────────────────────────────────────────────


def create_server(registry: SourceRegistry) -> Server[Any, Any]:
    """Build and return a configured ``mcp.server.Server`` instance.

    Registers the four deepferry tools (list_sources, list_tables,
    schema_info, query) with their input schemas and async handlers.
    All errors returned to agents are structured JSON — no Python tracebacks
    leak through the tool boundary.
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

            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({
                        "code": "UNKNOWN_TOOL",
                        "message": f"No handler registered for tool {name!r}",
                        "suggestion": (
                            "Available tools: list_sources, list_tables, schema_info, query"
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


async def run_stdio_server(registry: SourceRegistry) -> None:
    """Start the MCP server over stdin/stdout (for Claude Desktop etc.).

    Blocks until the stdio streams are closed.
    """
    app = create_server(registry)
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
) -> None:
    """Start the MCP server over Streamable HTTP.

    Builds a Starlette ASGI application backed by ``StreamableHTTPSessionManager``
    and serves it via uvicorn.  Suitable for remote MCP clients or local dev
    where stdio is not practical.
    """
    import uvicorn

    server = create_server(registry)
    session_manager = StreamableHTTPSessionManager(app=server)

    async def lifespan(  # noqa: ARG001
        starlette_app: starlette.applications.Starlette,
    ) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def mcp_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    starlette_app = starlette.applications.Starlette(
        lifespan=lifespan,  # type: ignore[arg-type]
        routes=[
            starlette.routing.Route("/", mcp_endpoint, methods=["GET", "POST"]),
            starlette.routing.Route("/{path:path}", mcp_endpoint, methods=["GET", "POST"]),
        ],
    )

    config = uvicorn.Config(
        starlette_app,
        host=host,
        port=port,
        log_level="info",
    )
    uvicorn_server = uvicorn.Server(config)
    await uvicorn_server.serve()
