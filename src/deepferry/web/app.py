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
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from deepferry.core.errors import SourceNotFoundError
from deepferry.core.models import SourceSummary
from deepferry.datasources.registry import SourceRegistry

_registry: SourceRegistry | None = None
"""Module-level registry reference, set by ``init_app()`` at startup."""


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


# ── Source discovery ────────────────────────────────────────────────────────


@app.get("/config/sources")
async def list_sources(
    registry: SourceRegistry = Depends(get_registry),
) -> list[SourceSummary]:
    """Return a summary of every registered data source.

    Includes source ID, name, type, and a health status string
    (``"healthy"``, ``"unhealthy"``, or ``"unknown"``).
    """
    return registry.list_sources()


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


def init_app(registry: SourceRegistry) -> FastAPI:
    """Initialise and return the FastAPI application with the given registry.

    Called by the CLI entry point after loading sources from ``config.toml``::

        from deepferry.web.app import init_app
        app = init_app(registry)
        uvicorn.run(app, host="127.0.0.1", port=8080)

    Parameters
    ----------
    registry : SourceRegistry
        A fully-loaded source registry (all sources connected).

    Returns
    -------
    FastAPI
        The configured application instance.
    """
    global _registry
    _registry = registry
    return app
