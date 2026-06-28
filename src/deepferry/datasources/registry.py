"""SourceRegistry — lifecycle manager for all DataSource instances.

The registry is the single entry point for discovering, instantiating, and
tracking every data source declared in ``config.toml``.  It supports hot-reload
via ``refresh()`` using a copy-on-write strategy so that in-flight queries are
never disrupted.

Registration pattern
--------------------
Concrete datasource modules call ``register_source_type()`` at import time.
This decouples the registry from any specific implementation — no hard imports
of ``MySQLDataSource`` or ``PostgreSQLDataSource``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from deepferry.core.errors import ConfigError, SourceNotFoundError
from deepferry.core.models import SourceSummary
from deepferry.datasources.base import DataSource

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

    import aiosqlite
    import httpx2

    from deepferry.auth.token_manager import TokenManager
    from deepferry.config import AppConfig, SourceConfig

logger = logging.getLogger(__name__)

# ── Plugin registration ───────────────────────────────────────────────────

_SOURCE_CLASSES: dict[str, type[DataSource]] = {}
"""Maps ``type`` field values from config.toml to concrete DataSource subclasses.

Populated by ``register_source_type()`` which datasource modules call at import
time.  Example entry: ``{"mysql": MySQLDataSource}``.
"""


def register_source_type(type_name: str, cls: type[DataSource]) -> None:
    """Register a concrete DataSource subclass for a given ``type`` string.

    Called at module import time by datasource implementations::

        from deepferry.datasources.registry import register_source_type
        register_source_type("mysql", MySQLDataSource)

    Parameters
    ----------
    type_name : str
        The value that appears in ``config.toml`` ``type = "..."`` fields.
    cls : type[DataSource]
        A concrete subclass of ``DataSource``.
    """
    if not issubclass(cls, DataSource):
        raise TypeError(
            f"{cls.__name__} must be a subclass of DataSource, "
            f"not {getattr(cls, '__bases__', ())}"
        )
    if type_name in _SOURCE_CLASSES:
        existing = _SOURCE_CLASSES[type_name]
        logger.warning(
            "Source type %r is already registered (%s). Overwriting with %s.",
            type_name,
            existing.__name__,
            cls.__name__,
        )
    _SOURCE_CLASSES[type_name] = cls
    logger.debug("Registered source type %r → %s", type_name, cls.__name__)


# ── Factory ────────────────────────────────────────────────────────────────


def _instantiate_source(
    config: SourceConfig,
    token_manager: TokenManager | None = None,
    registry: SourceRegistry | None = None,
) -> DataSource:
    """Create a DataSource instance from a SourceConfig entry.

    Looks up the concrete class via ``register_source_type``, instantiates it
    with the source-specific config, and assigns ``source_id``.

    For HTTP sources that declare two-step auth, *token_manager* is forwarded
    so the source can obtain/cache/refresh access tokens.  For custom sources
    (``type`` starts with ``"custom:"``), *registry* and its shared
    ``http_client`` are forwarded so the implementation can resolve composed
    sources by id and issue HTTP calls.  Other source types are instantiated
    with ``config`` only.

    Parameters
    ----------
    config : SourceConfig
        A single resolved source entry from ``AppConfig.sources``.
    token_manager : TokenManager | None
        Shared token manager owned by the registry.  Injected into HTTP
        sources; ignored by sources that do not accept it.
    registry : SourceRegistry | None
        Back-reference to the owning registry.  Injected into custom sources
        so they can resolve composed sources via ``registry.get(id)``; ignored
        by sources that do not accept it.

    Returns
    -------
    DataSource
        A fully-instantiated (but not yet connected) data source.

    Raises
    ------
    ConfigError
        When ``config.type`` does not match any registered source class.
    """
    cls = _SOURCE_CLASSES.get(config.type)
    if cls is None:
        registered = ", ".join(sorted(_SOURCE_CLASSES)) or "(none)"
        raise ConfigError(
            code="UNKNOWN_SOURCE_TYPE",
            message=f"No DataSource registered for type={config.type!r}. "
            f"Registered types: {registered}.",
            suggestion=f"Ensure the module for {config.type!r} is imported "
            f"and calls register_source_type().",
        )

    # Custom sources receive shared dependencies via constructor injection:
    # the shared http_client (same one used by TokenManager), a registry
    # back-reference for resolving composed sources, and a trace sink when one
    # is available.  The registry does not yet own a TraceSink, so None is
    # forwarded for now.
    if config.type.startswith("custom:"):
        shared_http_client = registry._token_http_client if registry is not None else None
        instance = cls(  # type: ignore[call-arg]
            config=config,
            http_client=shared_http_client,
            registry=registry,
            trace_sink=None,
        )
    elif config.type == "http" and token_manager is not None:
        instance = cls(config, token_manager=token_manager)  # type: ignore[call-arg]
    else:
        instance = cls(config)  # type: ignore[call-arg]
    instance.source_id = config.id
    return instance


# ── Registry ──────────────────────────────────────────────────────────────


class SourceRegistry:
    """In-memory registry that manages the full lifecycle of all data sources.

    Sources are loaded from ``AppConfig`` and can be refreshed at runtime
    without restarting the process.  The refresh uses **copy-on-write**:
    a new instances dict is built, then atomically swapped, so in-flight
    queries on old instances complete undisturbed.

    Typical usage
    -------------
    >>> registry = SourceRegistry()
    >>> await registry.load_from_config(config)
    >>> source = registry.get("my-mysql")
    >>> await registry.refresh()       # hot-reload config.toml
    >>> await registry.shutdown()      # graceful teardown
    """

    def __init__(self) -> None:
        self._instances: dict[str, DataSource] = {}
        self._drain_tasks: list[asyncio.Task[None]] = []
        self._config: AppConfig | None = None
        self._config_path: Path | None = None
        self._token_manager: TokenManager | None = None
        self._token_db: aiosqlite.Connection | None = None
        self._token_http_client: httpx2.AsyncClient | None = None

    # ── Initial load ────────────────────────────────────────────────────

    async def load_from_config(
        self, config: AppConfig, config_path: Path | None = None
    ) -> None:
        """Instantiate and connect all sources declared in *config*.

        Parameters
        ----------
        config : AppConfig
            The fully-resolved application configuration.
        config_path : Path | None
            Optional filesystem path to ``config.toml``.  When provided, it is
            remembered so that ``refresh()`` can re-parse the file on disk and
            pick up external edits (manual edits or REST API writes).
        """
        if config_path is not None:
            self._config_path = config_path
        self._config = config
        await self._ensure_token_manager(config)
        instances: dict[str, DataSource] = {}
        connect_tasks: list[Coroutine[None, None, None]] = []

        for source_cfg in config.sources:
            instance = _instantiate_source(
                source_cfg, self._token_manager, registry=self
            )
            instances[source_cfg.id] = instance

            async def _connect(src: DataSource) -> None:
                try:
                    await src.connect()
                    logger.info("Connected source %r (type=%s)", src.source_id, src.source_type)
                except Exception:
                    logger.exception("Failed to connect source %r", src.source_id)
                    raise

            connect_tasks.append(_connect(instance))

        # Connect all sources concurrently, fail-fast on any error.
        if connect_tasks:
            results = await asyncio.gather(*connect_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    source_id = config.sources[i].id
                    logger.error(
                        "Source %r failed to connect, aborting load.", source_id
                    )
                    # Best-effort disconnect sources already connected.
                    for j in range(i):
                        try:
                            await instances[config.sources[j].id].disconnect()
                        except Exception:
                            logger.exception(
                                "Error during rollback disconnect of %r",
                                config.sources[j].id,
                            )
                    raise ConfigError(
                        code="CONNECT_FAILED",
                        message=f"Source {source_id!r} failed to connect: {result}",
                    ) from result

        self._instances = instances
        logger.info(
            "Registry loaded %d source(s): %s",
            len(instances),
            ", ".join(sorted(instances)),
        )

    async def _ensure_token_manager(self, config: AppConfig) -> None:
        """Create the shared TokenManager if any HTTP source declares auth.

        Idempotent: reuses an existing token manager across ``refresh()``
        cycles so cached tokens survive hot-reloads. The shared SQLite
        connection and httpx2 client are owned by the registry and torn down
        in ``shutdown()``.
        """
        needs_auth = any(
            src.type == "http" and isinstance(src.extra.get("auth"), dict)
            for src in config.sources
        )
        if not needs_auth or self._token_manager is not None:
            return

        import os

        import httpx2

        from deepferry.auth.token_manager import TokenManager
        from deepferry.core.db import get_db, init_db

        db_path = os.environ.get(
            "DEEPFERRY_DB_PATH",
            os.path.join(os.path.expanduser("~"), ".deepferry", "deepferry.db"),
        )
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        await init_db(db_path)
        self._token_db = await get_db(db_path)
        self._token_http_client = httpx2.AsyncClient(timeout=30.0)
        self._token_manager = TokenManager(self._token_db, self._token_http_client)
        logger.info("TokenManager initialised (db=%s)", db_path)

    # ── Lookup ───────────────────────────────────────────────────────────

    def get(self, source_id: str) -> DataSource:
        """Return the DataSource instance for *source_id*.

        Parameters
        ----------
        source_id : str
            The source ID as declared in ``config.toml``.

        Returns
        -------
        DataSource
            The registered data source instance.

        Raises
        ------
        SourceNotFoundError
            When *source_id* is not registered.
        """
        instance = self._instances.get(source_id)
        if instance is None:
            available = ", ".join(sorted(self._instances)) or "(none)"
            raise SourceNotFoundError(
                code="SOURCE_NOT_FOUND",
                message=f"Source {source_id!r} is not registered.",
                suggestion=f"Available sources: {available}. "
                f"Check config.toml and ensure the source is defined.",
            )
        return instance

    def list_sources(self) -> list[SourceSummary]:
        """Return a summary of every registered source.

        Health is reported as ``"unknown"`` for sources that have not been
        explicitly health-checked since the last load.
        """
        summaries: list[SourceSummary] = []
        for src_id, instance in self._instances.items():
            summaries.append(
                SourceSummary(
                    id=src_id,
                    name=src_id,
                    type=instance.source_type,
                    health="unknown",
                )
            )
        return summaries

    # ── Hot reload ───────────────────────────────────────────────────────

    async def refresh(self) -> None:
        """Hot-reload ``config.toml`` and atomically swap the source set.

        Uses copy-on-write: builds a new instance map, connects new sources,
        disconnects removed sources, then swaps the internal pointer.
        In-flight queries on old instances are undisturbed.

        If ``config_path`` was recorded by ``load_from_config()`` or set
        explicitly, the file is re-parsed from disk first — so this method
        picks up both REST-API-driven writes (``POST /api/config/sources``)
        and manual edits to ``config.toml`` (triggered via
        ``POST /api/config/reload``).

        If the config path is not known (registry was not loaded via
        ``load_from_config``) and no config has been loaded, this is a no-op.
        """
        if self._config_path is not None:
            # Re-parse config.toml to pick up external changes (manual edits
            # or REST writes).  Local import avoids a module-level dependency
            # cycle: deepferry.config imports only deepferry.core.errors.
            from deepferry.config import load_config

            self._config = load_config(self._config_path)

        if self._config is None:
            logger.warning("refresh() called but no config has been loaded — skipping.")
            return
        await self._ensure_token_manager(self._config)
        new_instances: dict[str, DataSource] = {}
        for source_cfg in self._config.sources:
            new_instance = _instantiate_source(
                source_cfg, self._token_manager, registry=self
            )
            await new_instance.connect()
            new_instances[source_cfg.id] = new_instance

        # Determine diff.
        old_ids = set(self._instances)
        new_ids = set(new_instances)

        removed_ids = old_ids - new_ids
        added_ids = new_ids - old_ids
        kept_ids = old_ids & new_ids

        # For edited sources (same id, different config), treat as remove+add.
        edited_ids: set[str] = set()
        for sid in kept_ids:
            # Simple heuristic: if the old instance had a different source_type
            # the config changed.  A full implementation would compare config
            # hashes.
            if self._instances[sid].__class__ is not new_instances[sid].__class__:
                edited_ids.add(sid)

        removed_ids |= edited_ids
        added_ids |= edited_ids

        # Atomic swap.
        old_instances = self._instances
        self._instances = new_instances

        # Schedule graceful drain of removed/edited instances.
        to_drain = [old_instances[sid] for sid in removed_ids | edited_ids]
        if to_drain:
            logger.info(
                "Draining %d old source instance(s): %s",
                len(to_drain),
                ", ".join(s.source_id for s in to_drain),
            )
            self._drain_tasks.append(
                asyncio.create_task(self._drain_old_instances(to_drain))
            )

        logger.info(
            "Registry refreshed: %d added, %d removed, %d edited, %d unchanged.",
            len(added_ids - edited_ids),
            len(removed_ids - edited_ids),
            len(edited_ids),
            len(kept_ids - edited_ids),
        )

    async def _drain_old_instances(
        self, instances: list[DataSource], grace_period: float = 30.0
    ) -> None:
        """Give old instances time to finish in-flight queries, then disconnect."""
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(grace_period)
        for instance in instances:
            try:
                await instance.disconnect()
                logger.debug("Drained old source instance %r", instance.source_id)
            except Exception:
                logger.exception("Error disconnecting old source %r", instance.source_id)

    # ── Shutdown ─────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Disconnect all sources and cancel pending drain tasks.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        # Cancel any pending drain tasks.
        for task in self._drain_tasks:
            if not task.done():
                task.cancel()
        if self._drain_tasks:
            await asyncio.gather(*self._drain_tasks, return_exceptions=True)
        self._drain_tasks.clear()

        # Disconnect all active instances.
        for src_id, instance in list(self._instances.items()):
            try:
                await instance.disconnect()
                logger.debug("Disconnected source %r", src_id)
            except Exception:
                logger.exception("Error disconnecting source %r during shutdown", src_id)

        self._instances.clear()

        if self._token_http_client is not None:
            await self._token_http_client.aclose()
            self._token_http_client = None
        if self._token_db is not None:
            await self._token_db.close()
            self._token_db = None
        self._token_manager = None

        logger.info("Registry shutdown complete.")
