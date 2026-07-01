"""deepferry CLI — MCP-based universal data access layer."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import anyio

from deepferry.config import load_config
from deepferry.datasources.registry import SourceRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
logger = logging.getLogger("deepferry.cli")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deepferry",
        description="MCP-based universal data access layer for LLM agents.",
    )
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser(
        "mcp-server",
        help="Start the MCP server exposing configured data sources as tools",
    )
    mcp_parser.add_argument(
        "--config", required=True, type=Path, help="Path to config.toml"
    )
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    mcp_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind when --transport=http (default: 127.0.0.1)",
    )
    mcp_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind when --transport=http (default: 8000)",
    )

    args = parser.parse_args()

    if args.command == "mcp-server":
        anyio.run(_run_mcp_server, args)
    else:
        parser.print_help()


async def _run_mcp_server(args: argparse.Namespace) -> None:
    """Load config, instantiate the registry, and start the chosen transport."""
    # Import datasource modules so they call register_source_type().
    import deepferry.datasources.http_api  # noqa: F401
    import deepferry.datasources.mysql  # noqa: F401
    import deepferry.datasources.postgresql  # noqa: F401

    config = load_config(args.config)
    registry = SourceRegistry()
    await registry.load_from_config(config, config_path=args.config)

    from deepferry.engine.duckdb import DuckDBEngine

    engine = DuckDBEngine(registry)
    await engine.connect()

    try:
        if args.transport == "http":
            from deepferry.mcp_server.server import run_http_server

            logger.info(
                "Starting MCP server on http://%s:%d (Streamable HTTP)",
                args.host,
                args.port,
            )
            await run_http_server(
                registry,
                host=args.host,
                port=args.port,
                engine=engine,
                config_path=args.config,
                llm_config=config.llm,
            )
        else:
            from deepferry.mcp_server.server import run_stdio_server

            logger.info("Starting MCP server over stdio")
            await run_stdio_server(registry, engine=engine)
    finally:
        await engine.disconnect()
        await registry.shutdown()


if __name__ == "__main__":
    main()
