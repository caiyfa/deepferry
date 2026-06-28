"""Custom imperative data sources — code-level extension point.

When a fixed data flow is too cross-type or too rigid for the declarative
orchestration engine (e.g. it must interleave HTTP API calls with SQL queries,
or needs conditional/loop logic), encapsulate the entire flow as a custom
``DataSource`` class.  The agent still calls a single ``query()`` and receives
one ``StructuredResult``; the multi-step, mixed HTTP+DB logic lives in Python
inside ``execute()``.

Convention
----------
A custom source inherits :class:`~deepferry.datasources.base.DataSource`
directly (or :class:`CustomDataSource` below for shared safety helpers) and
implements all six abstract methods.  The ``source_type`` class attribute MUST
follow the ``"custom:<name>"`` convention so the registry can distinguish
custom sources from built-in types::

    class OrderEnrichmentDataSource(CustomDataSource):
        source_type: ClassVar[str] = "custom:order_enrichment"

        def __init__(self, config, http_client, registry, trace_sink=None):
            super().__init__(config=config, http_client=http_client,
                             registry=registry, trace_sink=trace_sink)
            ...

Registration is explicit — the implementing module must call
:func:`~deepferry.datasources.registry.register_source_type` at import time::

    from deepferry.datasources.registry import register_source_type
    register_source_type("custom:order_enrichment", OrderEnrichmentDataSource)

The registry recognises the ``"custom:"`` prefix and injects the shared
``http_client``, a back-reference to itself (so composed sources can be
resolved by id at runtime), and an optional ``trace_sink``.

Composition over inheritance
----------------------------
Composed sources (e.g. a ``MySQLDataSource`` reused inside a custom flow) are
*held* as dependencies, never subclassed.  Resolve them from the registry by
their configured ``source_id`` inside ``connect()``::

    async def connect(self) -> None:
        mysql_id = self._config.extra.get("mysql_source_id")
        if mysql_id is None:
            raise DataSourceError(...)
        self._mysql = self._registry.get(mysql_id)

Production safeguards (MANDATORY)
---------------------------------
Custom sources are NOT exempt from the production posture.  Every custom
``execute()`` MUST enforce:

1. **Read-only SQL legs** — block any statement that mutates state.  Use
   :meth:`CustomDataSource.enforce_read_only` on every SQL leg and back it
   with a read-only DB account.
2. **Timeout propagation** — ``QueryRequest.timeout`` must bound every leg
   (cumulative or per-leg).  A hung HTTP leg must never run forever.
3. **Output row cap** — cap rows via ``QueryRequest.max_rows`` and flag
   ``truncated=true`` in the result.  Use
   :meth:`CustomDataSource.truncate_rows`.
4. **HTTP response size cap** — cap bytes read on every HTTP leg.  Use
   :meth:`CustomDataSource.check_http_response_size`.
5. **Structured errors only** — every failure path raises
   :class:`~deepferry.core.errors.DataSourceError`.  Never leak a Python
   traceback to the agent (First Principle #5).
6. **Per-leg spans** — every HTTP/SQL leg opens an independent span in the
   ``TraceSink``.  A custom source without tracing is rejected in review.

See ``openspec/specs/custom-datasource.md`` for the full spec and decision
tree.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from deepferry.core.errors import DataSourceError
from deepferry.datasources.base import DataSource

if TYPE_CHECKING:
    import httpx2

    from deepferry.config import SourceConfig
    from deepferry.core.trace import TraceSink
    from deepferry.datasources.registry import SourceRegistry

# Keywords that indicate a SQL statement mutates state.  Matched case-insensitively
# at the start of the stripped statement OR after `;` (guard against stacked queries).
_WRITE_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "alter",
    "create",
    "replace",
    "grant",
    "revoke",
    "merge",
    "call",
    "set",
    "lock",
    "unlock",
    "vacuum",
    "analyze",  # may lock tables on some engines
)
_WRITE_SQL_RE = re.compile(
    r"(?:^|;)\s*(" + "|".join(_WRITE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Default safeguards — implementations may override via config.extra.
DEFAULT_MAX_ROWS = 10_000
DEFAULT_MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024  # 8 MiB


class CustomDataSource(DataSource):
    """Optional helper base class for custom imperative data sources.

    Provides constructor injection for shared dependencies and reusable safety
    helpers (read-only enforcement, row truncation, HTTP size caps).  Custom
    sources MAY inherit from this class for convenience, but the only hard
    contract is :class:`~deepferry.datasources.base.DataSource` — this class
    adds no new abstract methods.

    Subclasses MUST set ``source_type`` to a ``"custom:<name>"`` string and
    implement all six abstract methods of :class:`DataSource`.

    Parameters
    ----------
    config : SourceConfig
        The resolved ``[[sources]]`` entry from ``config.toml``.  Source-specific
        keys live in ``config.extra``.
    http_client : httpx2.AsyncClient | None
        Shared HTTP client owned by the registry.  May be ``None`` if no HTTP
        legs are needed; implementations that need HTTP should create their own
        client in ``connect()`` when ``None`` is passed.
    registry : SourceRegistry | None
        Back-reference to the registry, used to resolve composed sources by id
        at runtime.  May be ``None`` in unit tests.
    trace_sink : TraceSink | None
        Optional trace sink for per-leg span instrumentation.  When ``None``,
        tracing helpers are no-ops.
    """

    source_type: ClassVar[str] = "custom"

    def __init__(
        self,
        config: SourceConfig,
        http_client: httpx2.AsyncClient | None = None,
        registry: SourceRegistry | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        super().__init__()
        self._config: SourceConfig = config
        self._http_client: httpx2.AsyncClient | None = http_client
        self._registry: SourceRegistry | None = registry
        self._trace_sink: TraceSink | None = trace_sink

    # ── Public accessors ────────────────────────────────────────────────

    @property
    def config(self) -> SourceConfig:
        """The source config entry from ``config.toml``."""
        return self._config

    @property
    def http_client(self) -> httpx2.AsyncClient | None:
        """The shared HTTP client injected by the registry (may be ``None``)."""
        return self._http_client

    @property
    def registry(self) -> SourceRegistry | None:
        """The registry back-reference, for resolving composed sources."""
        return self._registry

    @property
    def trace_sink(self) -> TraceSink | None:
        """The trace sink for span instrumentation (may be ``None``)."""
        return self._trace_sink

    # ── Safety helpers ──────────────────────────────────────────────────

    @staticmethod
    def enforce_read_only(statement: str) -> None:
        """Reject SQL statements that mutate state.

        Scans the statement for write/DDL keywords (``INSERT``, ``UPDATE``,
        ``DELETE``, ``DROP``, ``TRUNCATE``, ``ALTER``, ``CREATE``, ...) at the
        start of the statement or after any ``;`` (to catch stacked queries).

        Parameters
        ----------
        statement : str
            The SQL statement to check.

        Raises
        ------
        DataSourceError
            With ``code="WRITE_FORBIDDEN"`` when a mutating keyword is found.
        """
        if _WRITE_SQL_RE.search(statement):
            raise DataSourceError(
                code="WRITE_FORBIDDEN",
                message=(
                    "Custom sources only accept read-only SQL statements. "
                    "The statement contains a forbidden keyword "
                    f"(INSERT/UPDATE/DELETE/DDL/etc.): {statement!r}"
                ),
                suggestion=(
                    "Use a SELECT query.  For writes, configure a dedicated "
                    "writable source outside the custom datasource contract."
                ),
            )

    @staticmethod
    def truncate_rows(
        rows: list[dict[str, Any]],
        max_rows: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Cap a row list at ``max_rows`` and report whether truncation occurred.

        Parameters
        ----------
        rows : list[dict[str, Any]]
            The full result set returned by a sub-query.
        max_rows : int | None
            The cap.  When ``None`` or larger than ``len(rows)``, no
            truncation is applied and ``False`` is returned.

        Returns
        -------
        tuple[list[dict[str, Any]], bool]
            The (possibly truncated) row list and a ``truncated`` flag.
        """
        limit = max_rows if max_rows is not None and max_rows >= 0 else DEFAULT_MAX_ROWS
        if len(rows) <= limit:
            return rows, False
        return rows[:limit], True

    @staticmethod
    def check_http_response_size(
        response: httpx2.Response,
        max_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES,
    ) -> None:
        """Reject HTTP responses larger than ``max_bytes``.

        Parameters
        ----------
        response : httpx2.Response
            The HTTP response to inspect.  ``Content-Length`` is checked
            first; if absent, the ``content`` length is used (already
            materialised by httpx).
        max_bytes : int
            The upper bound in bytes (default 8 MiB).

        Raises
        ------
        DataSourceError
            With ``code="RESPONSE_TOO_LARGE"`` when the response exceeds the cap.
        """
        content_length_header = response.headers.get("content-length")
        size = (
            int(content_length_header)
            if content_length_header and content_length_header.isdigit()
            else len(response.content)
        )
        if size > max_bytes:
            raise DataSourceError(
                code="RESPONSE_TOO_LARGE",
                message=(
                    f"HTTP response size {size} bytes exceeds cap {max_bytes} bytes."
                ),
                suggestion=(
                    "Narrow the request (pagination, filters) or raise "
                    "max_response_bytes in the source config.extra."
                ),
            )

    # ── Trace helpers (no-ops when trace_sink is None) ──────────────────

    def _trace_enabled(self) -> bool:
        """Return ``True`` when a trace sink is wired up."""
        return self._trace_sink is not None
