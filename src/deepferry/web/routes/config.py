"""Configuration CRUD endpoints — add/update/remove data sources at runtime.

Exposes a REST surface for mutating ``config.toml`` without restarting the
process.  Every write goes through three phases:

1. **Persist** — the new / edited / removed ``[[sources]]`` block is written
   to disk via the helpers in :mod:`deepferry.config`.  Comments, blank
   lines, and unrelated blocks are preserved.
2. **Refresh** — ``registry.refresh()`` re-parses ``config.toml``, builds a
   new instances map, and atomically swaps it in (copy-on-write, so in-flight
   queries on old instances are undisturbed).
3. **Acknowledge** — the caller receives a small JSON envelope identifying
   the affected source and the new state.

``POST /api/config/reload`` is a lighter-weight variant: it skips step 1 and
just re-parses the file — useful when a human edits ``config.toml`` directly
in an editor and wants the running process to pick up the changes.

The router is mounted under the ``/config`` prefix *inside* the FastAPI app,
which is in turn mounted under ``/api`` by the MCP HTTP server (see
``server.py``'s ``Mount("/api", app=config_app)``).  The fully-qualified URL
is therefore ``/api/config/...``.

All routes depend on ``get_registry`` / ``get_config_path`` from
:mod:`deepferry.web.app`.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deepferry.config import (
    SourceConfig,  # noqa: TC001  # used as parameter annotation
    remove_source_from_config,
    update_source_in_config,
    write_source_to_config,
)
from deepferry.core.errors import ConfigError, SourceNotFoundError
from deepferry.core.models import SourceSummary
from deepferry.datasources.registry import (
    SourceRegistry,  # noqa: TC001  # used as parameter annotation
)
from deepferry.web.app import get_config_path, get_registry

router = APIRouter(prefix="/config", tags=["config"])


class SourceCreateRequest(BaseModel):
    """JSON body for ``POST`` / ``PUT /api/config/sources``.

    Only top-level ``[[sources]]`` fields are exposed.  Source-specific extras
    (e.g. ``auth``, ``resources``, ``steps``) cannot be set via this API —
    edit ``config.toml`` directly and call ``POST /api/config/reload``.
    """

    id: str
    type: str
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    base_url: str | None = None


def _to_source_config(source_id: str, body: SourceCreateRequest) -> SourceConfig:
    """Build a ``SourceConfig`` from a request body, using *source_id* as id.

    Kept as a helper so ``POST`` (body-driven id) and ``PUT`` (path-driven id)
    can share the exact same mapping logic.
    """
    return SourceConfig(
        id=source_id,
        type=body.type,
        name=body.name,
        host=body.host,
        port=body.port,
        database=body.database,
        user=body.user,
        password=body.password,
        base_url=body.base_url,
    )


@router.get("/sources")
async def list_sources(
    registry: SourceRegistry = Depends(get_registry),
) -> list[SourceSummary]:
    """Return a summary of every registered data source.

    Includes source ID, name, type, and a health status string
    (``"healthy"``, ``"unhealthy"``, or ``"unknown"``).
    """
    return registry.list_sources()


@router.post("/sources")
async def add_source(
    body: SourceCreateRequest,
    registry: SourceRegistry = Depends(get_registry),
    config_path: Path = Depends(get_config_path),
) -> dict[str, str]:
    """Append a new ``[[sources]]`` block to ``config.toml`` and refresh.

    Returns ``{"id": ..., "status": "active"}`` on success.  If a source with
    the same id already exists, the TOML write still appends a duplicate
    block — callers should ``PUT`` to update instead.
    """
    source = _to_source_config(body.id, body)
    try:
        write_source_to_config(config_path, source)
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    try:
        await registry.refresh()
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    return {"id": body.id, "status": "active"}


@router.get("/sources/{source_id}")
async def get_source(
    source_id: str,
    registry: SourceRegistry = Depends(get_registry),
) -> dict[str, str]:
    """Return a one-line summary of a single registered source."""
    try:
        source = registry.get(source_id)
    except SourceNotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    return {"id": source.source_id, "type": source.source_type}


@router.put("/sources/{source_id}")
async def update_source(
    source_id: str,
    body: SourceCreateRequest,
    registry: SourceRegistry = Depends(get_registry),
    config_path: Path = Depends(get_config_path),
) -> dict[str, str]:
    """Replace the ``[[sources]]`` block with id ``source_id`` and refresh.

    The path parameter supplies the id of the block to replace; the request
    body supplies the new field values.  The id field in the body is ignored
    in favour of the path parameter (REST-style resource identity).
    """
    source = _to_source_config(source_id, body)
    try:
        update_source_in_config(config_path, source_id, source)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": str(e)},
        ) from e
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    try:
        await registry.refresh()
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    return {"id": source_id, "status": "updated"}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str,
    registry: SourceRegistry = Depends(get_registry),
    config_path: Path = Depends(get_config_path),
) -> dict[str, str]:
    """Remove the ``[[sources]]`` block with id ``source_id`` and refresh.

    Returns ``{"id": ..., "status": "removed"}``.  The source is first looked
    up in the live registry so a missing id yields a structured 404 before
    any disk mutation is attempted.
    """
    try:
        registry.get(source_id)  # verify the source is registered
    except SourceNotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    try:
        remove_source_from_config(config_path, source_id)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": str(e)},
        ) from e
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    try:
        await registry.refresh()
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    return {"id": source_id, "status": "removed"}


@router.post("/reload")
async def reload_config(
    registry: SourceRegistry = Depends(get_registry),
) -> dict[str, str | int]:
    """Re-parse ``config.toml`` and hot-swap the source set.

    Use this after editing ``config.toml`` by hand (or via any other writer
    outside this process).  Returns the new source count so callers can
    verify the reload took effect.
    """
    try:
        await registry.refresh()
    except ConfigError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    return {"status": "reloaded", "source_count": len(registry.list_sources())}
