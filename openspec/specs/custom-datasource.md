# Capability: Custom Imperative Data Sources

> **Status**: planned | **Milestone**: M2.5+ (extension guide) | **Owner**: backend | **Depends on**: `mvp-init` M1+

## Summary

A code-level extension point: when a fixed data flow is **too cross-type or too
rigid** for the declarative orchestration engine (e.g. it must interleave HTTP
API calls with SQL queries, or needs conditional/loop logic), encapsulate the
entire flow as a custom `DataSource` class. The agent still calls a single
`query()` and receives one `StructuredResult`; the multi-step, mixed HTTP+DB
logic lives in Python inside `execute()`.

## Motivation

deepferry offers four ways to get data, layered by expressiveness vs. effort:

| # | Path | Form | Mixes HTTP+DB? | Logic allowed | Who writes |
|---|------|------|-----------------|---------------|------------|
| 1 | Direct SQL / HTTP source | config only | ❌ | none | user (config.toml) |
| 2 | `OrchestratedDataSource` | declarative TOML steps | ❌ (HTTP-only steps) | linear + foreach | user (config.toml) |
| 3 | Agent multi-turn orchestration | agent loop | ✅ (across calls) | full (agent reasons) | agent + prompts |
| 4 | **Custom `DataSource` (this spec)** | **Python class** | ✅ (in one `execute()`)** | **full (it's code)** | **developer (new module)** |

The declarative engine (path 2) deliberately rejects `if`/`while`/DAG and
HTTP+DB mixing — that keeps config reviewable and avoids becoming Airflow
(see [[orchestration]] § design.md decision #3). But some enterprise flows are
**fixed, always-the-same, and inherently cross-type**: *"login to the billing
API → pull invoice IDs → look up customer details in MySQL → POST each enriched
record to the reconciliation API."* Re-running that flow through the agent
every time (path 3) wastes LLM cost and relies on multi-hop reliability; the
declarative engine cannot express it. Path 4 is the answer: write it once, in
Python, as a `DataSource`.

**This is not a contradiction of "don't mix HTTP+DB in orchestration."** The
orchestration *engine* stays declarative and HTTP-only (reviewable, safe). The
custom *DataSource* is a different extension layer — a new source type, written
in code, reviewed/ tested like any other module, that happens to mix transports
internally. The ABC contract ([[datasource-abstraction]]) is preserved: the MCP
server and registry never `isinstance`-branch on it.

## Specification

### When to Encapsulate (Decision Tree)

Encapsulate as a custom `DataSource` when **all** are true:
- The flow is **fixed** — it runs the same steps every time (not agent-driven).
- It **mixes source types** (HTTP + DB) or needs **logic the declarative engine
  rejects** (conditional, loop, mid-flow aggregation).
- It is **repeated often** enough that per-run agent cost/latency/reliability
  is a real concern.

Otherwise prefer a lower path:
- Only HTTP, linear/foreach → `OrchestratedDataSource` ([[orchestration]]).
- Dynamic, agent-decided, small data → agent multi-turn ([[mcp-server]] §
  Multi-Turn Agent Orchestration).
- Cross-source JOIN of existing data → DuckDB federation
  ([[duckdb-cross-source]]).

### Implementation Contract

A custom source inherits `DataSource` ([[datasource-abstraction]]) and
implements the same five abstract methods. The constructor receives its
declared dependencies — this mirrors how `OrchestratedDataSource` is wired
(`config`, `http_client`, `token_manager`, `trace_sink`):

```python
class DataSource(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def execute(self, query: QueryRequest) -> StructuredResult: ...
    async def list_resources(self) -> list[Resource]: ...
    async def schema_info(self, resource: str | None = None) -> Schema: ...
    async def health_check(self) -> HealthStatus: ...
```

Constructor injection (resolved by the registry from configured clients):

```python
class OrderEnrichmentDataSource(DataSource):
    def __init__(
        self,
        config: OrderEnrichmentConfig,        # Pydantic model, parsed from [[sources]]
        http_client: httpx.AsyncClient,        # shared httpx2 client
        mysql: MySQLDataSource,                # composed, not inherited
        trace_sink: TraceSink,                 # for span instrumentation
    ): ...
```

### Reference Implementation (mixed HTTP + DB + trace)

A fixed flow: *fetch invoice IDs from a billing API → enrich with customer data
from MySQL → POST each enriched record to a reconciliation API.* The agent sees
one `query()` returning the reconciled rows; every internal leg is traced.

```python
from deepferry.core.errors import DataSourceError
from deepferry.core.models import QueryRequest, StructuredResult, ColumnMeta, Resource

class OrderEnrichmentDataSource(DataSource):
    """Fixed cross-type flow: billing API → MySQL enrichment → reconcile API."""

    def __init__(self, config, http_client, mysql, trace_sink):
        self._config = config
        self._http = http_client
        self._mysql = mysql                      # composed DataSource (read-only)
        self._trace = trace_sink

    async def connect(self) -> None:
        await self._mysql.connect()              # ensure DB pool is up

    async def disconnect(self) -> None:
        await self._mysql.disconnect()

    async def health_check(self) -> HealthStatus:
        # cheap check: billing API login ping only
        ...

    async def list_resources(self) -> list[Resource]:
        return [Resource(name="reconciled_orders", type="flow",
                         description="Billing × MySQL customer enrichment")]

    async def schema_info(self, resource: str | None = None) -> Schema:
        # declared statically in code — the flow's output schema is fixed
        ...

    async def execute(self, query: QueryRequest) -> StructuredResult:
        # root span — kind=orchestration (this IS an orchestrated flow, in code)
        exec_id = await self._trace.start_execution(
            self._config.id, root_query_id=None)
        root_span = await self._trace.start_span(
            exec_id, parent_id=None, name="order_enrichment", kind="orchestration")
        try:
            # Leg 1 — HTTP: fetch invoice IDs (token injected by TokenManager)
            s1 = await self._trace.start_span(
                exec_id, root_span, "billing.fetch_invoices", "http_request")
            invoices = await self._fetch_invoices(query.params)
            await self._trace.finish_span(s1, "ok", {"count": len(invoices)})

            # Leg 2 — SQL: enrich with customer details (read-only, bounded)
            s2 = await self._trace.start_span(
                exec_id, root_span, "mysql.enrich_customers", "sql_exec")
            ids = [inv["cust_id"] for inv in invoices]
            enriched = await self._mysql.execute(QueryRequest(
                source_id=self._mysql.id,
                statement="SELECT id, name, tier FROM customers WHERE id IN :ids",
                params={"ids": ids},
                timeout=query.timeout,
            ))
            await self._trace.finish_span(
                s2, "ok", {"rows": enriched.row_count})

            # Leg 3 — HTTP: POST each enriched record to reconciliation
            # (a foreach, expressed naturally in Python)
            s3 = await self._trace.start_span(
                exec_id, root_span, "reconcile.post", "http_request")
            posted = await self._post_reconciliation(enriched.rows, invoices)
            await self._trace.finish_span(s3, "ok", {"posted": posted})

            await self._trace.finish_span(root_span, "ok", {})
            return StructuredResult(
                columns=[ColumnMeta(name="cust_id", type="BIGINT"),
                         ColumnMeta(name="name", type="VARCHAR"),
                         ColumnMeta(name="reconciled", type="BOOLEAN")],
                rows=posted, row_count=len(posted),
                execution_time_ms=...,
            )
        except Exception as e:
            # structured error — agent/user never sees a Python traceback
            await self._trace.finish_span(root_span, "error", {"error": str(e)})
            raise DataSourceError(
                code="ENRICHMENT_FAILED",
                message=f"Order enrichment flow failed: {e}",
                suggestion="Check billing API health and MySQL connectivity",
            )
```

Notes on the skeleton:
- **Composition over inheritance**: `MySQLDataSource` is *composed* (held as a
  dependency), not subclassed. The custom source is its own `DataSource`; it
  delegates SQL to the composed source. This keeps each source single-purpose.
- **TraceSink is mandatory**: every leg opens a child span. Without tracing the
  flow is a black box — the exact problem [[audit-trace]] exists to solve. The
  span `kind` reuses the standard enum (`orchestration`/`http_request`/
  `sql_exec`) so the desktop timeline renders it natively.
- **One `execute()`, one result**: the agent and MCP layer are unaware of the
  internal legs. The output schema is fixed and declared in code.

### Configuration & Registration

Register via the `type` field. A custom source's extra config keys live under
its `[[sources]]` block and are parsed by the source's Pydantic config model.
The convention for the `type` value is `custom:<module>` to make clear this is
not a built-in source:

```toml
[[sources]]
id = "order-enrichment"
type = "custom:order_enrichment"        # maps to a registered CustomSource class
billing_api_url = "https://billing.internal/v1"
reconcile_api_url = "https://reconcile.internal/v1"
mysql_source_id = "prod-mysql"          # composed source reference

[sources.auth]                          # reused from two-step-auth if the flow needs login
login_path = "/auth/login"
# ...
```

The registry resolves `type` to a class. Built-in types (`mysql`, `postgresql`,
`http`, `orchestrated`) ship with deepferry; custom types are registered via an
entry point or an explicit registry call at startup (TBD in M2.5 — see
[[orchestration]] § tasks). Composed sources (e.g. `mysql_source_id`) are
resolved by id from the same config and injected into the constructor.

### Production Safeguards

Custom sources are **not exempt** from the production posture
([[duckdb-cross-source]] § Production Posture). Every custom `execute()` MUST:

- Enforce **read-only** on any SQL leg (read-only DB account + keyword block).
- Honor `QueryRequest.timeout` across **all** legs (cumulative or per-leg, but
  bounded) — a hung HTTP leg must not run forever.
- Cap output rows (`max_rows`) and flag `truncated=true`.
- Cap HTTP response size (`max_response_bytes`) on every HTTP leg.
- Surface failures as structured `DataSourceError` — never raw tracebacks
  (AGENTS.md First Principle #5).
- Open spans for every leg — a custom source with no tracing is rejected in
  review.

### Testing

- Unit-test each leg in isolation (mock HTTP, mock composed MySQL).
- Integration-test the full `execute()` against a mock billing API + test MySQL
  + mock reconcile API; assert the merged `StructuredResult` and the span tree
  shape (1 execution, 1 root + 3 child spans).
- Assert a leg failure raises `DataSourceError` with the right `code`, and the
  failing span is marked `error` with prior legs' spans intact.

## Acceptance Criteria

1. A developer can ship a custom `DataSource` mixing HTTP + DB by implementing
   the 5 ABC methods + a Pydantic config, registering under `type =
   "custom:<module>"`.
2. The agent calls `query(source_id="order-enrichment", ...)` and receives a
   single merged `StructuredResult`; it is unaware of the internal HTTP/SQL legs.
3. The desktop `/history/:id` view renders the custom source's execution as a
   root `orchestration` span with child `http_request` / `sql_exec` spans —
   indistinguishable in shape from an `OrchestratedDataSource` trace.
4. A failing HTTP or SQL leg raises a structured `DataSourceError` (no
   traceback); prior completed legs remain visible in the span tree.
5. Read-only, timeout, and row-cap safeguards are enforced on the SQL leg;
   response-size cap on HTTP legs.
6. Composing an existing `MySQLDataSource` (rather than reimplementing SQL)
   takes <1 day; writing a brand-new transport from scratch takes <2 days.

## Dependencies

- [[datasource-abstraction]] — the `DataSource` ABC and models
- [[audit-trace]] — `TraceSink` API used to instrument legs
- [[sql-datasource]] / [[http-api-datasource]] — composed sources reused inside
  a custom flow
- [[two-step-auth]] — `TokenManager` for any HTTP leg needing login
- [[orchestration]] — sibling extension point; this spec is the code-level
  alternative when the declarative engine cannot express the flow
