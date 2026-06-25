"""DataSource abstract base class — the plugin contract for all data sources.

Every data source implementation (MySQL, PostgreSQL, HTTP API, orchestrated
flows, custom imperative sources) must inherit this ABC and implement all six
abstract methods.  The registry sets ``source_id`` after instantiation so that
implementations can reference their own identity.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from deepferry.core.models import HealthStatus, QueryRequest, Resource, Schema, StructuredResult


class DataSource(ABC):
    """Unified interface for all data sources.

    Subclasses MUST define ``source_type`` as a class-level attribute (e.g.
    ``source_type: ClassVar[str] = "mysql"``).  The registry uses this value to
    map ``type`` fields in ``config.toml`` to concrete implementations.

    ``source_id`` is set by the registry after instantiation — implementations
    should treat it as read-only.

    Lifecycle
    ---------
    1.  Registry instantiates the source with its typed config.
    2.  Registry assigns ``source_id``.
    3.  Registry calls ``connect()`` → pool/connection established.
    4.  Agent calls ``execute`` / ``list_resources`` / ``schema_info`` / ``health_check``.
    5.  Registry calls ``disconnect()`` → pool drained, connections closed.
    """

    source_type: ClassVar[str] = ""

    def __init__(self) -> None:
        self._source_id: str = ""

    @property
    def source_id(self) -> str:
        """The source ID as declared in ``config.toml`` (set by the registry)."""
        return self._source_id

    @source_id.setter
    def source_id(self, value: str) -> None:
        if self._source_id and self._source_id != value:
            raise ValueError(
                f"Cannot reassign source_id: already set to {self._source_id!r}"
            )
        self._source_id = value

    # ── Abstract interface ──────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection pool or persistent session.

        Called once by the registry after instantiation.  Implementations should
        be idempotent — calling ``connect()`` on an already-connected source
        should be a safe no-op or raise a clear error.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close all connections and release resources.

        Called by the registry during shutdown or when a source is removed via
        ``refresh()``.  Must be safe to call multiple times.
        """
        ...

    @abstractmethod
    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Execute a query against the data source.

        Parameters
        ----------
        query : QueryRequest
            Contains ``statement`` (SQL or source-specific query text),
            optional ``params``, and a ``timeout`` in seconds.

        Returns
        -------
        StructuredResult
            Columns, rows, row count, and execution time in milliseconds.

        Raises
        ------
        DataSourceError
            Wrapped with a structured ``code``, ``message``, and ``suggestion``.
        """
        ...

    @abstractmethod
    async def list_resources(self) -> list[Resource]:
        """Discover all queryable resources (tables, views, endpoints).

        For SQL sources this typically runs ``SHOW TABLES`` or queries the
        information schema.  For HTTP sources it returns statically-configured
        endpoints.  Orchestrated sources discover resources dynamically at
        runtime.

        Returns
        -------
        list[Resource]
            Every resource known to the source at call time.
        """
        ...

    @abstractmethod
    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level schema metadata.

        Parameters
        ----------
        resource : str | None
            Name of the resource to introspect.  When ``None``, returns the
            schema for *all* resources (or a reasonable subset).

        Returns
        -------
        Schema
            One ``ResourceMeta`` per requested resource, each containing its
            ``ColumnMeta`` list.
        """
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check connectivity and responsiveness of the data source.

        Implementations should use a lightweight probe (e.g. ``SELECT 1`` for
        SQL sources, a ``GET /health`` for HTTP sources) and return latency.

        Returns
        -------
        HealthStatus
            ``ok`` is ``True`` when the probe succeeded within the timeout.
            ``error`` is populated with a short message on failure.
        """
        ...
