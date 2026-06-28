"""FastAPI application skeleton for the deepferry config panel (M1).

Provides the foundational API surface: health check, source discovery, and
per-source connectivity testing.  The full configuration panel UI is M3 work.

The app follows the ``init_app()`` pattern — the CLI creates a
``SourceRegistry``, loads sources, and injects the registry before starting
uvicorn.  All routes are async and use FastAPI's dependency injection for the
registry.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import Depends, FastAPI, HTTPException

from deepferry.core.errors import SourceNotFoundError
from deepferry.core.trace import TraceSink
from deepferry.datasources.registry import SourceRegistry

_registry: SourceRegistry | None = None
"""Module-level registry reference, set by ``init_app()`` at startup."""

_db: aiosqlite.Connection | None = None
"""Module-level SQLite connection (optional), set by ``init_app()``."""

_trace_sink: TraceSink | None = None
"""Module-level trace sink (optional), set by ``init_app()``."""

_config_path: Path | None = None
"""Module-level path to ``config.toml`` (optional), set by ``init_app()``.

Used by the config CRUD endpoints (``POST /api/config/sources`` etc.) to write
mutations back to disk so they survive restarts and are visible to any other
process reading the same file.
"""


def get_registry() -> SourceRegistry:
    """FastAPI dependency that returns the current ``SourceRegistry``.

    Raises ``RuntimeError`` if ``init_app()`` has not been called yet — this
    indicates a startup configuration bug, not a recoverable error.
    """
    if _registry is None:
        raise RuntimeError(
            "Registry not initialized — call init_app(registry) before starting the server."
        )
    return _registry


def get_db() -> aiosqlite.Connection | None:
    """FastAPI dependency returning the optional SQLite connection.

    Returns ``None`` when no database is configured (read-only deployments that
    skip query-history / trace persistence).
    """
    return _db


def get_trace_sink() -> TraceSink | None:
    """FastAPI dependency returning the optional ``TraceSink``.

    Returns ``None`` when tracing is not configured.
    """
    return _trace_sink


def get_config_path() -> Path:
    """FastAPI dependency returning the path to ``config.toml`` on disk.

    Used by the config CRUD endpoints (``POST/PUT/DELETE /api/config/sources``)
    so they can persist mutations.  Raises ``RuntimeError`` if ``init_app()``
    was not called with ``config_path`` — config mutations are disabled in
    that case.
    """
    if _config_path is None:
        raise RuntimeError(
            "Config path not set — call init_app() with config_path to enable "
            "config CRUD endpoints."
        )
    return _config_path


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """FastAPI lifespan context manager.

    Startup: the registry is already initialised by the CLI via ``init_app()``.
    Shutdown: no-op for now; cleanup is handled by the CLI's shutdown hook.
    """
    yield


app = FastAPI(
    title="deepferry",
    description="MCP-based universal data access layer — config panel API",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health ──────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns 200 when the process is running."""
    return {"status": "ok"}


# ── Source testing ──────────────────────────────────────────────────────────


@app.post("/config/sources/{source_id}/test")
async def test_source(
    source_id: str,
    registry: SourceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Run a health-check against a single data source.

    Returns the ``HealthStatus`` (``ok``, ``latency_ms``, optional ``error``)
    serialised as JSON.  A missing source yields a structured 404; connection
    failures yield a structured 500.
    """
    try:
        source = registry.get(source_id)
        result = await source.health_check()
        return result.model_dump()
    except SourceNotFoundError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.to_dict(),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "TEST_FAILED",
                "message": str(e),
            },
        ) from e


# ── Bootstrap ───────────────────────────────────────────────────────────────


def init_app(
    registry: SourceRegistry,
    db: aiosqlite.Connection | None = None,
    trace_sink: TraceSink | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    """Initialise and return the FastAPI application with the given registry.

    Called by the CLI entry point after loading sources from ``config.toml``::

        from deepferry.web.app import init_app
        app = init_app(registry)
        uvicorn.run(app, host="127.0.0.1", port=8080)

    Parameters
    ----------
    registry : SourceRegistry
        A fully-loaded source registry (all sources connected).
    db : aiosqlite.Connection | None
        Optional SQLite connection for query-history persistence.  When
        ``None``, history-recording endpoints degrade gracefully.
    trace_sink : TraceSink | None
        Optional trace sink for execution tracing.  When ``None``, trace
        endpoints degrade gracefully.
    config_path : Path | None
        Optional path to ``config.toml`` on disk.  When provided, the config
        CRUD endpoints (``POST/PUT/DELETE /api/config/sources``) become
        functional and persist mutations back to this file.  When ``None``,
        those endpoints raise ``RuntimeError`` on call.

    Returns
    -------
    FastAPI
        The configured application instance.
    """
    global _registry, _db, _trace_sink, _config_path
    _registry = registry
    _db = db
    _trace_sink = trace_sink
    _config_path = config_path

    # Imported lazily here (not at module top-level) to avoid a circular
    # import: every route module imports ``get_registry`` / ``get_db`` /
    # ``get_trace_sink`` from this module.
    from deepferry.web.routes.config import router as config_router
    from deepferry.web.routes.executions import router as executions_router
    from deepferry.web.routes.history import router as history_router
    from deepferry.web.routes.query import router as query_router
    from deepferry.web.routes.schema import router as schema_router

    app.include_router(query_router)
    app.include_router(schema_router)
    app.include_router(history_router)
    app.include_router(executions_router)
    app.include_router(config_router)

    return app
