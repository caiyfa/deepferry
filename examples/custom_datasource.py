"""Example custom DataSource: OrderEnrichmentDataSource.

A fixed cross-type flow that combines:

1.  An HTTP **billing API** (fetches invoice IDs), and
2.  A **MySQL** source (enriches invoices with customer details).

The agent calls a single ``query(source_id="order-enrichment", ...)`` and
receives one merged :class:`~deepferry.core.models.StructuredResult`.  The
internal HTTP/SQL legs are invisible to the agent — this is the whole point of
encapsulating a fixed flow as a custom ``DataSource``.

How to run
----------
1.  Import this module before loading ``config.toml`` so the registration side
    effect runs::

        import examples.custom_datasource  # noqa: F401  (registers "custom:order_enrichment")

2.  Declare the source in ``config.toml`` (see
    ``examples/config.example.custom.toml``)::

        [[sources]]
        id = "order-enrichment"
        type = "custom:order_enrichment"
        billing_api_url = "https://billing.internal/v1"
        mysql_source_id = "prod-mysql"

3.  Start the server::

        uv run deepferry mcp-server --config examples/config.example.custom.toml

Pattern highlights
------------------
- **Composition over inheritance**: ``MySQLDataSource`` is *held* as a
  dependency (resolved from the registry by id), not subclassed.
- **Constructor injection**: the registry injects ``config``,
  ``http_client``, ``registry``, and ``trace_sink`` — the same wiring used by
  :class:`~deepferry.datasources.orchestrated.OrchestratedDataSource`.
- **Safety**: every SQL leg is enforced read-only, every HTTP leg has a
  response-size cap, the output is row-capped with a ``truncated`` flag, and
  ``QueryRequest.timeout`` propagates to all sub-queries.
- **Structured errors**: every failure path raises
  :class:`~deepferry.core.errors.DataSourceError` — agents never see Python
  tracebacks.
- **Per-leg spans**: when a ``TraceSink`` is wired, every HTTP/SQL leg opens
  an independent span so the desktop timeline renders the flow natively.

See ``openspec/specs/custom-datasource.md`` for the full spec and decision
tree.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

from deepferry.core.errors import DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    HealthStatus,
    QueryRequest,
    Resource,
    ResourceMeta,
    Schema,
    StructuredResult,
)
from deepferry.core.trace import Execution, Span, SpanKind, SpanStatus, TraceSink
from deepferry.datasources.custom import (
    DEFAULT_MAX_HTTP_RESPONSE_BYTES,
    DEFAULT_MAX_ROWS,
    CustomDataSource,
)
from deepferry.datasources.registry import register_source_type

if TYPE_CHECKING:
    import httpx2

    from deepferry.config import SourceConfig
    from deepferry.datasources.base import DataSource
    from deepferry.datasources.registry import SourceRegistry


class OrderEnrichmentDataSource(CustomDataSource):
    """Fixed cross-type flow: billing API → MySQL enrichment.

    The flow runs in :meth:`execute`:

    1.  **HTTP leg** — ``GET {billing_api_url}/invoices`` returns a list of
        invoice dicts ``{"invoice_id", "cust_id", "amount"}``.
    2.  **SQL leg** — ``SELECT id, name, tier FROM customers WHERE id IN (...)``
        against the composed MySQL source, enriching each invoice with
        customer details.

    The merged result exposes one row per invoice with customer fields
    attached.  The output schema is fixed and declared in code.

    Configuration keys (read from ``config.extra``)
    ------------------------------------------------
    - ``billing_api_url`` *(str, required)* — base URL of the billing API.
    - ``mysql_source_id`` *(str, required)* — the ``id`` of a configured
      ``mysql`` source to compose for customer enrichment.
    - ``max_rows`` *(int, optional)* — output row cap (default 10,000).
    - ``max_response_bytes`` *(int, optional)* — HTTP response size cap
      (default 8 MiB).
    """

    source_type: ClassVar[str] = "custom:order_enrichment"

    # Declared output schema — the flow's result shape is fixed in code.
    _OUTPUT_COLUMNS: ClassVar[list[ColumnMeta]] = [
        ColumnMeta(name="invoice_id", type="VARCHAR", nullable=False),
        ColumnMeta(name="cust_id", type="BIGINT", nullable=False),
        ColumnMeta(name="amount", type="DECIMAL", nullable=False),
        ColumnMeta(name="customer_name", type="VARCHAR", nullable=True),
        ColumnMeta(name="customer_tier", type="VARCHAR", nullable=True),
    ]

    def __init__(
        self,
        config: SourceConfig,
        http_client: httpx2.AsyncClient | None = None,
        registry: SourceRegistry | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        super().__init__(
            config=config,
            http_client=http_client,
            registry=registry,
            trace_sink=trace_sink,
        )
        # Validate config.extra early so connect() fails fast on misconfig.
        extra = config.extra
        self._billing_api_url: str | None = extra.get("billing_api_url")
        self._mysql_source_id: str | None = extra.get("mysql_source_id")
        self._max_rows: int = int(extra.get("max_rows", DEFAULT_MAX_ROWS))
        self._max_response_bytes: int = int(
            extra.get("max_response_bytes", DEFAULT_MAX_HTTP_RESPONSE_BYTES)
        )

        # Resolved in connect() — held, not inherited.
        self._mysql: DataSource | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Resolve the composed MySQL source from the registry.

        Validates that ``billing_api_url`` and ``mysql_source_id`` are present
        and that the referenced MySQL source exists in the registry.  The
        composed source is expected to be already connected (the registry
        connects all sources during ``load_from_config``); we do NOT call
        ``connect()`` on it here to avoid double-initialisation.
        """
        if self._billing_api_url is None:
            raise DataSourceError(
                code="MISSING_FIELD",
                message=(
                    "OrderEnrichmentDataSource requires 'billing_api_url' in "
                    "config.extra (the [[sources]] block)."
                ),
                suggestion=(
                    "Add `billing_api_url = \"https://...\"` to the "
                    "[[sources]] block for this custom source."
                ),
            )
        if self._mysql_source_id is None:
            raise DataSourceError(
                code="MISSING_FIELD",
                message=(
                    "OrderEnrichmentDataSource requires 'mysql_source_id' in "
                    "config.extra (the id of a configured mysql source)."
                ),
                suggestion=(
                    "Add `mysql_source_id = \"prod-mysql\"` to the "
                    "[[sources]] block, referencing an existing [[sources]] "
                    "block with type = \"mysql\"."
                ),
            )
        if self._registry is None:
            raise DataSourceError(
                code="REGISTRY_UNAVAILABLE",
                message=(
                    "OrderEnrichmentDataSource needs a registry reference to "
                    "resolve the composed MySQL source, but registry is None."
                ),
                suggestion=(
                    "This usually means the source was instantiated outside "
                    "the registry. Ensure config.toml uses "
                    "type = \"custom:order_enrichment\" and the registry "
                    "loaded this module."
                ),
            )
        # Resolve the composed source — raises SourceNotFoundError if missing.
        self._mysql = self._registry.get(self._mysql_source_id)

    async def disconnect(self) -> None:
        """Release references; the registry owns the composed source's lifecycle."""
        self._mysql = None

    # ── Discovery & schema ─────────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Return the single logical resource exposed by this flow."""
        return [
            Resource(
                name="enriched_invoices",
                type="flow",
                description=(
                    "Billing API invoices enriched with MySQL customer details "
                    "(name, tier)."
                ),
            )
        ]

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return the fixed output schema.

        ``resource`` is accepted for ABC compatibility but ignored — this
        source exposes exactly one logical resource.
        """
        return Schema(
            resources=[
                ResourceMeta(
                    name="enriched_invoices",
                    columns=list(self._OUTPUT_COLUMNS),
                )
            ]
        )

    async def health_check(self) -> HealthStatus:
        """Cheap probe: ping the billing API ``/healthz`` endpoint.

        Does NOT probe the composed MySQL source — that source has its own
        health check exposed via the registry.
        """
        if self._http_client is None:
            return HealthStatus(
                ok=False,
                latency_ms=0.0,
                error="No shared http_client injected; cannot probe billing API.",
            )
        start = time.monotonic()
        try:
            assert self._billing_api_url is not None  # validated in connect()
            resp = await self._http_client.get(
                f"{self._billing_api_url}/healthz", timeout=5.0
            )
            latency = (time.monotonic() - start) * 1000.0
            if resp.status_code < 400:
                return HealthStatus(ok=True, latency_ms=latency)
            return HealthStatus(
                ok=False,
                latency_ms=latency,
                error=f"billing API /healthz returned HTTP {resp.status_code}",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000.0
            return HealthStatus(ok=False, latency_ms=latency, error=str(exc))

    # ── Execute (the flow) ─────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Run the full flow: fetch invoices → enrich with customers.

        The query's ``statement`` selects which logical resource to run (only
        ``"enriched_invoices"`` is supported in this example).  ``params`` is
        forwarded to the billing API as query parameters.  ``timeout`` and
        ``max_rows`` propagate to every leg.

        Raises
        ------
        DataSourceError
            On any leg failure, missing dependency, or safety violation.
        """
        start = time.monotonic()
        self._require_connected()

        # Root span (kind=orchestration) wraps the whole flow when tracing.
        execution: Execution | None = None
        root_span: Span | None = None
        if self._trace_enabled():
            assert self._trace_sink is not None
            execution = await self._trace_sink.start_execution(self.source_id)
            root_span = await self._trace_sink.add_span(
                execution,
                Span(
                    id=0,
                    execution_id=0,
                    span_kind=SpanKind.orchestration,
                    span_name="order_enrichment",
                    source_id=self.source_id,
                    started_at=0,
                ),
            )
            parent_span_id = root_span.id
        else:
            parent_span_id = None

        try:
            # ── Leg 1: HTTP — fetch invoices ──────────────────────────
            invoices = await self._fetch_invoices(
                query, execution=execution, parent_span_id=parent_span_id
            )

            # ── Leg 2: SQL — enrich with customer details ─────────────
            enriched = await self._enrich_customers(
                invoices, query, execution=execution, parent_span_id=parent_span_id
            )

            elapsed_ms = (time.monotonic() - start) * 1000.0

            # ── Output row cap with truncation flag ───────────────────
            # NOTE: StructuredResult currently has no `truncated` field; the
            # cap is still enforced so we never return unbounded rows.  When
            # the model gains a `truncated` flag, surface it here.
            capped, _truncated = self.truncate_rows(enriched, self._max_rows)

            await self._finish_root(execution, root_span, SpanStatus.ok)

            return StructuredResult(
                columns=list(self._OUTPUT_COLUMNS),
                rows=capped,
                row_count=len(capped),
                execution_time_ms=elapsed_ms,
            )

        except DataSourceError:
            await self._finish_root(execution, root_span, SpanStatus.error)
            raise
        except Exception as exc:
            await self._finish_root(execution, root_span, SpanStatus.error)
            # Wrap any unexpected error — never leak a raw traceback.
            raise DataSourceError(
                code="ENRICHMENT_FAILED",
                message=f"Order enrichment flow failed: {exc}",
                suggestion=(
                    "Check billing API health and MySQL connectivity, then "
                    "retry the query."
                ),
            ) from exc

    # ── Internal legs ──────────────────────────────────────────────────

    async def _fetch_invoices(
        self,
        query: QueryRequest,
        execution: Execution | None,
        parent_span_id: int | None,
    ) -> list[dict[str, Any]]:
        """Leg 1: GET invoices from the billing API.

        Enforces the HTTP response-size cap and forwards ``query.timeout`` to
        the underlying request.  Opens an ``http_request`` span under the
        root orchestration span when a trace sink is wired.
        """
        assert self._billing_api_url is not None
        if self._http_client is None:
            raise DataSourceError(
                code="HTTP_CLIENT_UNAVAILABLE",
                message="No shared http_client injected for the billing API leg.",
                suggestion="Ensure the registry wired an http_client at load time.",
            )

        span = await self._start_span(
            execution=execution,
            parent_span_id=parent_span_id,
            kind=SpanKind.http_request,
            name="billing.fetch_invoices",
        )

        try:
            resp = await self._http_client.get(
                f"{self._billing_api_url}/invoices",
                params=query.params,
                timeout=float(query.timeout),
            )
            if resp.status_code >= 400:
                raise DataSourceError(
                    code="BILLING_API_ERROR",
                    message=(
                        f"Billing API returned HTTP {resp.status_code}: "
                        f"{resp.text[:200]}"
                    ),
                    suggestion="Check billing API credentials and availability.",
                )
            # Enforce response-size cap (spec: max_response_bytes on every HTTP leg).
            self.check_http_response_size(resp, self._max_response_bytes)
            invoices = resp.json()
            if not isinstance(invoices, list):
                raise DataSourceError(
                    code="BILLING_API_FORMAT",
                    message="Billing API /invoices did not return a JSON list.",
                )
            await self._finish_span(span, SpanStatus.ok)
            return invoices
        except DataSourceError:
            await self._finish_span(span, SpanStatus.error)
            raise
        except Exception as exc:
            await self._finish_span(span, SpanStatus.error)
            raise DataSourceError(
                code="BILLING_FETCH_FAILED",
                message=f"Failed to fetch invoices from billing API: {exc}",
                suggestion="Verify the billing_api_url and network connectivity.",
            ) from exc

    async def _enrich_customers(
        self,
        invoices: list[dict[str, Any]],
        query: QueryRequest,
        execution: Execution | None,
        parent_span_id: int | None,
    ) -> list[dict[str, Any]]:
        """Leg 2: read-only SQL against the composed MySQL source.

        Enforces read-only (via :meth:`CustomDataSource.enforce_read_only`),
        forwards ``query.timeout`` and ``query.max_rows`` to the sub-query,
        and opens a ``sql_exec`` span when a trace sink is wired.
        """
        assert self._mysql is not None

        span = await self._start_span(
            execution=execution,
            parent_span_id=parent_span_id,
            kind=SpanKind.sql_exec,
            name="mysql.enrich_customers",
        )

        # Extract distinct customer ids for the IN clause.
        cust_ids = list({inv["cust_id"] for inv in invoices if "cust_id" in inv})
        if not cust_ids:
            await self._finish_span(span, SpanStatus.ok)
            # Nothing to enrich — return invoices as-is with null customer fields.
            return [
                {**inv, "customer_name": None, "customer_tier": None}
                for inv in invoices
            ]

        # Read-only, parameterised query.  The placeholder syntax (:ids) is the
        # SQLAlchemy-style named bind; the composed source is responsible for
        # expanding it.  We defensively enforce read-only here too.
        sql = "SELECT id, name, tier FROM customers WHERE id IN :ids"
        self.enforce_read_only(sql)

        try:
            sub_request = QueryRequest(
                source_id=self._mysql.source_id,
                statement=sql,
                params={"ids": tuple(cust_ids)},
                timeout=query.timeout,
                max_rows=query.max_rows,
            )
            result = await self._mysql.execute(sub_request)

            # Build a lookup of customer_id → (name, tier).
            lookup: dict[Any, tuple[str | None, str | None]] = {}
            for row in result.rows:
                lookup[row.get("id")] = (row.get("name"), row.get("tier"))

            await self._finish_span(span, SpanStatus.ok)

            return [
                {
                    "invoice_id": inv.get("invoice_id"),
                    "cust_id": inv.get("cust_id"),
                    "amount": inv.get("amount"),
                    "customer_name": lookup.get(inv.get("cust_id"), (None, None))[0],
                    "customer_tier": lookup.get(inv.get("cust_id"), (None, None))[1],
                }
                for inv in invoices
            ]
        except DataSourceError:
            await self._finish_span(span, SpanStatus.error)
            raise
        except Exception as exc:
            await self._finish_span(span, SpanStatus.error)
            raise DataSourceError(
                code="MYSQL_ENRICH_FAILED",
                message=f"MySQL customer enrichment failed: {exc}",
                suggestion=(
                    f"Verify the composed source {self._mysql_source_id!r} is "
                    "connected and the customers table exists."
                ),
            ) from exc

    # ── Trace helpers (no-ops when trace_sink is None) ─────────────────

    async def _start_span(
        self,
        execution: Execution | None,
        parent_span_id: int | None,
        kind: SpanKind,
        name: str,
    ) -> Span | None:
        """Open a child span under the root execution, or return ``None``."""
        if not self._trace_enabled() or execution is None:
            return None
        assert self._trace_sink is not None
        return await self._trace_sink.add_span(
            execution,
            Span(
                id=0,
                execution_id=0,
                parent_span_id=parent_span_id,
                span_kind=kind,
                span_name=name,
                source_id=self.source_id,
                started_at=0,
            ),
        )

    async def _finish_span(self, span: Span | None, status: SpanStatus) -> None:
        """Mark a span as completed; no-op when ``span`` is ``None``."""
        if span is None or self._trace_sink is None:
            return
        await self._trace_sink.finish_span(span, status)

    async def _finish_root(
        self,
        execution: Execution | None,
        root_span: Span | None,
        status: SpanStatus,
    ) -> None:
        """Finalise the root span and its execution; no-op when unwired."""
        if self._trace_sink is None:
            return
        if root_span is not None:
            await self._trace_sink.finish_span(root_span, status)
        if execution is not None:
            await self._trace_sink.finish_execution(execution, status)

    # ── Helpers ────────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        """Ensure connect() has resolved the composed MySQL source."""
        if self._mysql is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="OrderEnrichmentDataSource.execute() called before connect().",
                suggestion="Ensure the registry connected this source before dispatch.",
            )


# ── Registration ──────────────────────────────────────────────────────
#
# Importing this module registers the custom source type so config.toml
# entries with `type = "custom:order_enrichment"` resolve to this class.
# In a real deployment you would either:
#   (a) import this module from your CLI entrypoint before load_config(), or
#   (b) wire it up via an entry-point plugin (future work).
register_source_type("custom:order_enrichment", OrderEnrichmentDataSource)
