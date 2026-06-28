"""OrchestratedDataSource — multi-step request flows as a single logical DataSource.

This module now contains only the :class:`OrchestratedDataSource` ``DataSource``
implementation and the legacy :class:`StepBinding` helper.  The orchestration
engine, models, interpolation, and validation logic live in the
:mod:`deepferry.orchestration` package and are re-exported here for backward
compatibility with existing import sites.

From the MCP agent's perspective, an orchestrated source is indistinguishable
from any other DataSource — one ``query()`` call, one ``StructuredResult``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar

import httpx2
from pydantic import BaseModel

from deepferry.core.errors import ConfigError, DataSourceError
from deepferry.core.models import (
    HealthStatus,
    QueryRequest,
    Resource,
    ResourceMeta,
    Schema,
    StructuredResult,
)
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import register_source_type
from deepferry.orchestration import (
    ForeachConfig,
    OrchestrationConfig,
    OrchestrationEngine,
    Step,
    _detect_cycles,
    _detect_undefined_bindings,
    interpolate,
    interpolate_dict,
    validate_orchestration_config,
)

if TYPE_CHECKING:
    from deepferry.config import SourceConfig
    from deepferry.core.trace import TraceSink

# Re-export so legacy ``from deepferry.datasources.orchestrated import ...``
# call sites keep resolving.  These names are intentionally exposed as the
# public surface of this module.
__all__ = [
    "ForeachConfig",
    "OrchestrationConfig",
    "OrchestrationEngine",
    "OrchestratedDataSource",
    "Step",
    "StepBinding",
    "_detect_cycles",
    "_detect_undefined_bindings",
    "interpolate",
    "interpolate_dict",
    "validate_orchestration_config",
]


class StepBinding(BaseModel):
    """Declares how a step's output feeds into the query input.

    Legacy helper retained for the configuration panel and external callers.
    """

    step_name: str
    """The step whose output is used as the query input binding."""

    output_key: str
    """The key within that step's output."""


class OrchestratedDataSource(DataSource):
    """A ``DataSource`` backed by a multi-step request flow.

    Implements all six ABC methods.  From the MCP agent's perspective, an
    orchestrated source is indistinguishable from any other source — one
    ``execute()`` call, one ``StructuredResult``.

    The orchestration flow is declared in ``config.toml`` via
    ``[[sources.steps]]`` blocks, which land in ``SourceConfig.extra["steps"]``.
    Both the spec-style step fields (``path``, ``body_template``, ``output``,
    per-step ``foreach``) and the legacy fields (``url``, ``body``,
    ``output_key``, top-level ``foreach``) are accepted.
    """

    source_type: ClassVar[str] = "orchestrated"

    def __init__(
        self,
        config: SourceConfig,
        http_client: httpx2.AsyncClient | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._http_client = http_client
        self._owns_client = http_client is None
        self._engine: OrchestrationEngine | None = None
        self._trace = trace_sink
        self._discovered: list[Resource] | None = None
        self._discovery_ts: float = 0.0
        self._discovery_ttl: int = config.extra.get("discovery_ttl_seconds", 300)

        # Build the orchestration config eagerly for validation
        self._orchestration_config = self._build_config()

    # ── Config building ─────────────────────────────────────────────────

    def _build_config(self) -> OrchestrationConfig:
        """Build an :class:`OrchestrationConfig` from ``SourceConfig.extra``.

        Called at ``__init__`` time so that validation errors (cycle detection,
        undefined bindings) are raised at startup, not at query time.
        """
        steps_raw = self._config.extra.get("steps", [])
        if not steps_raw:
            raise ConfigError(
                code="MISSING_FIELD",
                message=f"Orchestrated source {self._config.id!r} has no steps defined.",
                suggestion="Add [[sources.steps]] blocks in config.toml.",
            )

        steps: list[Step] = []
        for raw in steps_raw:
            steps.append(Step(**raw))

        foreach_config: ForeachConfig | None = None
        foreach_raw = self._config.extra.get("foreach")
        if foreach_raw:
            foreach_config = ForeachConfig(**foreach_raw)

        config = OrchestrationConfig(
            id=self._config.id,
            base_url=self._config.base_url or self._config.extra.get("base_url"),
            steps=steps,
            foreach=foreach_config,
            discovery_ttl_seconds=self._config.extra.get(
                "discovery_ttl_seconds", 300
            ),
        )

        # Validate eagerly
        validate_orchestration_config(config)

        return config

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the HTTP client if one was not injected.

        Idempotent — calling on an already-connected source is a no-op.
        """
        if self._engine is not None:
            return
        if self._http_client is None:
            self._http_client = httpx2.AsyncClient(
                base_url=self._config.base_url or "",
                timeout=30.0,
            )
            self._owns_client = True
        self._engine = OrchestrationEngine(self._http_client, self._trace)

    async def disconnect(self) -> None:
        """Close the HTTP client if owned by this source.

        Idempotent — safe to call multiple times.
        """
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        self._engine = None

    # ── Query execution ─────────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Execute the orchestration flow and return the final result.

        *query.statement* is bound as ``{{sql}}`` in step templates.
        *query.params* are bound as ``{{params}}``.

        Parameters
        ----------
        query : QueryRequest
            The query to execute, with SQL in ``statement``.

        Returns
        -------
        StructuredResult
            The merged result from the orchestration flow.

        Raises
        ------
        DataSourceError
            With codes ``STEP_FAILED``, ``INVALID_BINDING``, or
            ``FOREACH_SCHEMA_MISMATCH``.
        """
        if self._engine is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message=f"Orchestrated source {self._config.id!r} is not connected.",
                suggestion="Call connect() before executing queries.",
            )

        inputs: dict[str, Any] = {
            "sql": query.statement,
        }
        if query.params:
            inputs["params"] = query.params

        return await self._engine.run(self._orchestration_config, inputs)

    # ── Resource discovery ──────────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Return the dynamic resource list discovered at runtime.

        If no discovery has occurred or the cache TTL has expired, runs the
        flow up to and including the first step whose output yields an array
        of resource-like objects.

        Returns
        -------
        list[Resource]
            The dynamically-discovered resources.
        """
        # Static resources from config (fallback)
        static_resources_raw = self._config.extra.get("resources", [])
        if static_resources_raw:
            return [Resource(**r) for r in static_resources_raw]

        # Dynamic discovery
        if self._discovered is not None and time.time() - self._discovery_ts < self._discovery_ttl:
            return self._discovered

        # Run discovery: execute flow up to (and including) the first step
        # whose output is a list
        if self._engine is None:
            return []

        base = self._config.base_url or ""
        config = self._orchestration_config
        context: dict[str, Any] = {}

        for step in config.steps:
            await self._engine._execute_step(step, context, base, config, None, None)

            data: Any = context.get(step.name)
            if data is None and step.output_key:
                data = context.get(step.output_key)
            # For output-dict steps, the aliases live under step.name as a dict;
            # look for a list among the extracted alias values.
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, list):
                        data = value
                        break

            if isinstance(data, list):
                resources: list[Resource] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("id") or item.get("name") or str(item)
                    rtype = item.get("type", "endpoint")
                    desc = item.get("description")
                    resources.append(Resource(name=name, type=rtype, description=desc))
                self._discovered = resources
                self._discovery_ts = time.time()
                return resources

        # No discovery step found — return what we have
        return self._discovered or []

    # ── Schema info ─────────────────────────────────────────────────────

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level schema metadata.

        Samples a query with ``LIMIT 1`` to infer schema from the response,
        since orchestrated sources don't have static schema introspection.

        Parameters
        ----------
        resource : str | None
            Name of the resource to introspect.  When ``None``, returns the
            schema for all discovered resources.

        Returns
        -------
        Schema
            One ``ResourceMeta`` per resource with inferred columns.
        """
        if self._engine is None:
            return Schema(resources=[])

        # Try to run a minimal query to infer schema
        try:
            result = await self.execute(
                QueryRequest(
                    source_id=self._config.id,
                    statement="SELECT 1",
                    timeout=10,
                )
            )
            return Schema(resources=[
                ResourceMeta(
                    name=resource or "default",
                    columns=result.columns,
                )
            ])
        except DataSourceError:
            return Schema(resources=[])

    # ── Health check ────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Check connectivity by probing the base URL.

        Returns
        -------
        HealthStatus
            ``ok`` is ``True`` when the base URL responds within the timeout.
        """
        if self._http_client is None:
            try:
                client = httpx2.AsyncClient(timeout=10.0)
                start = time.perf_counter()
                resp = await client.get(self._config.base_url or "/")
                elapsed_ms = (time.perf_counter() - start) * 1000
                await client.aclose()
                return HealthStatus(
                    ok=resp.status_code < 500,
                    latency_ms=round(elapsed_ms, 2),
                    error=None if resp.status_code < 500 else f"HTTP {resp.status_code}",
                )
            except Exception as exc:
                return HealthStatus(
                    ok=False,
                    latency_ms=0,
                    error=str(exc),
                )

        start = time.perf_counter()
        try:
            resp = await self._http_client.get(
                self._config.base_url or "/",
                timeout=10.0,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            return HealthStatus(
                ok=resp.status_code < 500,
                latency_ms=round(elapsed_ms, 2),
                error=None if resp.status_code < 500 else f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return HealthStatus(
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )


# ── Registration ─────────────────────────────────────────────────────────

register_source_type("orchestrated", OrchestratedDataSource)
