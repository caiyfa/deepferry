# Capability: Execution Trace & Audit

> **Status**: planned | **Milestone**: M2.5 | **Owner**: backend | **Depends on**: `mvp-init` M1+

## Summary

A span-based execution trace model that records **how** every query result is
produced — not just *that* a query ran. Every `DataSource.execute()` call opens
one execution with one or more spans; orchestrated and cross-source queries
produce multi-level span trees. Traces persist in the existing SQLite `app.db`
alongside `query_history` and `token_cache`, and are surfaced to humans via the
desktop execution-detail view. The span schema is OpenTelemetry-aligned so
future export to an OTLP backend is mechanical.

## Motivation

- `query_history` (from `config-panel.md`) records only the **final outcome** of
  a single flat query: `source_id, query_text, row_count, status`. For a 3-step
  orchestrated flow or a cross-source DuckDB join, the intermediate steps
  (login, discover, materialize, join) leave **zero trace**. The result is a
  black box — unacceptable for ops review, compliance, or debugging.
- Token refresh events, 401 retries, foreach iterations, and partial failures
  are operationally critical and currently invisible.
- Multi-source results need **provenance**: when a desktop user sees a joined
  rowset, they must be able to answer "which source contributed which columns,
  and how long did each leg take?".
- The agent itself should stay simple (it receives one `StructuredResult`); the
  *human* needs the full process. Audit is a human-facing concern, layered
  behind the MCP tool boundary.

## Specification

### Trace Model

One **execution** per `DataSource.execute()` call. An execution owns a tree of
**spans**. The shape mirrors OpenTelemetry: each span has a parent, a kind,
timestamps, and attributes.

```python
class SpanKind(str, Enum):
    orchestration = "orchestration"   # root of an orchestrated flow
    sql_exec       = "sql_exec"       # direct SQL execute()
    http_request   = "http_request"   # HTTP datasource / orchestration step
    auth           = "auth"           # login / token acquisition
    auth_retry     = "auth_retry"     # reactive 401 re-login + retry
    materialize    = "materialize"    # DuckDB JSON → view (M4)
    join           = "join"           # DuckDB cross-source JOIN (M4)
    discover       = "discover"       # orchestrated dynamic resource discovery

class SpanStatus(str, Enum):
    ok    = "ok"
    error = "error"

class Execution(BaseModel):
    id: int
    root_query_id: int | None   # FK -> query_history.id (nullable for ad-hoc)
    source_id: str
    started_at: int             # unix ms
    finished_at: int | None
    status: SpanStatus
    total_spans: int
    root_sql: str | None        # the agent-facing statement, if any

class Span(BaseModel):
    id: int
    execution_id: int
    parent_span_id: int | None
    name: str                   # step name / "mysql.execute" / etc.
    kind: SpanKind
    started_at: int
    finished_at: int | None
    duration_ms: float | None
    status: SpanStatus
    # OTel-shaped attributes bag (flat string key -> JSON-serializable value)
    attributes: dict[str, Any]
    error: str | None
```

### Storage Schema (SQLite `app.db`)

Append-only, single-writer. Lives in the existing runtime tier alongside
`query_history` and `token_cache` (see `project.md` Storage Layer — three-tier
separation is preserved; traces are runtime metadata).

```sql
CREATE TABLE query_executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    root_query_id   INTEGER,                    -- FK -> query_history.id
    source_id       TEXT NOT NULL,
    session_id      TEXT,                       -- MCP Mcp-Session-Id (coarse correlation)
    scenario_id     TEXT,                       -- agent-opened investigation scenario (fine correlation)
    started_at      INTEGER NOT NULL,           -- unix ms
    finished_at     INTEGER,
    status          TEXT NOT NULL,              -- 'ok' | 'error'
    total_spans     INTEGER NOT NULL DEFAULT 0,
    root_sql        TEXT,
    FOREIGN KEY (root_query_id) REFERENCES query_history(id)
);
CREATE INDEX idx_executions_source ON query_executions(source_id, started_at);
CREATE INDEX idx_executions_status ON query_executions(status);

CREATE TABLE execution_spans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id    INTEGER NOT NULL,
    parent_span_id  INTEGER,                    -- NULL = root span
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,              -- SpanKind enum value
    started_at      INTEGER NOT NULL,
    finished_at     INTEGER,
    duration_ms     REAL,
    status          TEXT NOT NULL,              -- 'ok' | 'error'
    attributes      TEXT,                       -- JSON object, summary only
    error           TEXT,
    FOREIGN KEY (execution_id) REFERENCES query_executions(id),
    FOREIGN KEY (parent_span_id) REFERENCES execution_spans(id)
);
CREATE INDEX idx_spans_execution ON execution_spans(execution_id);
CREATE INDEX idx_spans_parent    ON execution_spans(parent_span_id);

CREATE TABLE query_scenarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id     TEXT UNIQUE NOT NULL,        -- agent-held handle
    session_id      TEXT NOT NULL,               -- MCP session that opened it
    label           TEXT,                        -- optional human label ("Q3 refund analysis")
    started_at      INTEGER NOT NULL,
    ended_at        INTEGER,
    query_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open' -- 'open' | 'closed'
);
CREATE INDEX idx_scenarios_session ON query_scenarios(session_id, started_at);
CREATE INDEX idx_executions_scenario ON query_executions(scenario_id);
```

**Payload policy**: spans store **summaries**, never raw request/response
bodies. Default attributes per kind:

| Kind | Default attributes |
|------|--------------------|
| `sql_exec` | `source_id`, `row_count`, `dialect` |
| `http_request` | `method`, `path`, `status_code`, `row_count` |
| `auth` | `token_type`, `cached` (bool), `expires_in` |
| `auth_retry` | `trigger_status` (401), `relogin` (bool), `attempt` (1) |
| `materialize` | `endpoint_name`, `row_count`, `strategy` ("lazy"\|"paginated"\|"explicit") |
| `join` | `left_source`, `right_source`, `row_count` |
| `orchestration` | `step_count`, `foreach` (bool) |
| `discover` | `resource_count` |

Raw payload capture is **opt-in per source** via `[sources.X].trace.capture_payload
= true` (default false). When enabled, a redacted excerpt (max 4KB, secrets
stripped) is stored in `attributes.payload_excerpt`.

### Secret Redaction

Headers and bodies matching a denylist are scrubbed before any attribute is
written:

| Pattern | Redacted to |
|---------|-------------|
| `Authorization`, `Cookie`, `Set-Cookie`, `X-*-Token` headers | `[REDACTED]` |
| JSON keys matching `password`, `secret`, `token`, `api_key` (case-insensitive) | `[REDACTED]` |
| `${ENV_VAR}` expansions in logged config | `[ENV]` |

Redaction runs in the `TraceSink` writer, not at call sites, so no instrumented
code can accidentally bypass it.

### TraceSink API

The single ingestion point. All `DataSource` implementations and the
orchestration engine call it; nothing writes span tables directly.

```python
class TraceSink:
    async def start_execution(
        self, source_id: str, root_sql: str | None = None, root_query_id: int | None = None
    ) -> int:  # returns execution_id
        ...

    async def start_span(
        self, execution_id: int, name: str, kind: SpanKind,
        parent_span_id: int | None = None, attributes: dict | None = None,
    ) -> int:  # returns span_id
        ...

    async def finish_span(
        self, span_id: int, status: SpanStatus,
        attributes: dict | None = None, error: str | None = None,
    ) -> None:
        ...

    async def finish_execution(self, execution_id: int, status: SpanStatus) -> None: ...

    # Scenario correlation (see [[mcp-server]] § Scenario Correlation)
    async def start_scenario(
        self, session_id: str, label: str | None = None
    ) -> str:  # returns scenario_id
        ...

    async def attach_to_scenario(self, execution_id: int, scenario_id: str) -> None:
        """Stamp an execution with its scenario_id; increments scenario.query_count."""
        ...

    async def end_scenario(self, scenario_id: str) -> None:
        """Mark scenario closed (idempotent; reopening via new query reopens)."""
        ...
```

`start_span` / `finish_span` are separate (not a context manager) so
long-running steps can be open while siblings proceed. A thin
`async with trace.span(...)` helper wraps the pair for the common case.

### Instrumentation Points

Every `DataSource.execute()` path is instrumented. The instrumentation is the
contract — a source that does not emit spans is non-conformant.

| Instrumented site | Span opened |
|-------------------|-------------|
| `MySQLDataSource.execute` | one `sql_exec` root span |
| `PostgreSQLDataSource.execute` | one `sql_exec` root span |
| `HttpApiDataSource.execute` | one `http_request` root span |
| `OrchestratedDataSource.execute` | one `orchestration` root + one per step + foreach grandchildren |
| `TokenManager.get_token` (login path) | one `auth` span |
| Reactive 401 retry (see [[two-step-auth]]) | one `auth_retry` span under the step span |
| DuckDB materialize (M4) | one `materialize` span |
| DuckDB cross-source JOIN (M4) | one `join` span |

A direct single-source SQL query therefore produces `1 execution + 1 span`. An
orchestrated 3-step flow with foreach over 2 instances and a 401 retry produces
`1 execution + 8 spans` (root + 3 steps + 2 foreach grandchildren on the query
step + 1 auth + 1 auth_retry). The tree makes the process legible at a glance.

### Retention

Traces grow unbounded without pruning. Default policy:

- `execution_spans` older than `[audit].retention_days` (default 30) are deleted
  on server start and every 24h.
- `query_executions` whose spans are all pruned are removed in the same pass.
- `query_history` is **not** pruned by this job (it has its own lifecycle) —
  executions merely reference it via nullable FK; a pruned execution leaves the
  history row intact.

### Query API (FastAPI)

Human-facing read paths. The MCP agent does not consume these.

| Endpoint | Returns |
|----------|---------|
| `GET /api/executions?source_id=&status=&limit=&offset=` | Paginated execution list |
| `GET /api/executions/:id` | Execution + nested span tree (one round trip) |
| `GET /api/executions/:id/spans/:span_id` | Single span detail (attributes expanded) |
| `GET /api/scenarios?session_id=&status=&limit=&offset=` | Paginated scenario list |
| `GET /api/scenarios/:id` | Scenario + all its executions (grouped review) |
| `POST /api/scenarios` / `POST /api/scenarios/:id/close` | Open / close a scenario (also exposed as MCP `start_scenario` / `end_scenario`) |

### Structured Logging

Span attributes are the **structured** record; the text log is the
**operational** stream.二者互补：

- Spans → SQLite → queryable, replayable, the system of record for "what
  happened in this query".
- Structured logs (JSON lines, `[audit].log_level` configurable) → stderr/file
  → for live troubleshooting, startup diagnostics, crashes that never reach the
  span writer.

Every span `start`/`finish` emits a correlated log line with `execution_id`,
`span_id`, `kind`, so the two streams can be joined on IDs during
post-incident review.

## Acceptance Criteria (M2.5)

1. A single `MySQLDataSource.execute` produces exactly 1 execution + 1
   `sql_exec` span, queryable via `GET /api/executions/:id`.
2. An orchestrated 3-step flow produces 1 execution + ≥3 spans with correct
   parent linkage (root → steps → foreach grandchildren).
3. A reactive 401 retry produces an `auth_retry` child span with
   `attributes.attempt = 1`, nested under the failing step's span.
4. Secret redaction: an `Authorization: Bearer <jwt>` header never appears in
   any span attribute (verify via grep on stored JSON).
5. `trace.capture_payload = true` stores a ≤4KB redacted excerpt; `false`
   (default) stores no payload.
6. Retention prunes spans older than `[audit].retention_days`; executions with
   zero remaining spans are removed; `query_history` rows survive.
7. A 50-span execution renders via `GET /api/executions/:id` in <100ms (index
   coverage verified).
8. `start_scenario` returns a `scenario_id`; queries carrying it stamp
   `query_executions.scenario_id` and increment `query_scenarios.query_count`.
9. `GET /api/scenarios/:id` returns the scenario + all its executions in one
   round trip (enabling the desktop grouped view).
10. Queries with no `scenario_id` still execute and trace under `session_id`
    alone — scenario correlation is optional, not mandatory.
11. `ruff check .` and `mypy --strict` pass; TraceSink + redaction coverage >80%.

## Interview Story

" Everyone says 'make it observable' and reaches for OpenTelemetry + Jaeger.
I deliberately did not. deepferry is a single-machine data ferry; a collector
sidecar is operations overhead disproportionate to the value. Instead I stole
OTel's *shape* — trace/span/parent/attributes/kind — and stored it in the SQLite
runtime DB we already have. The schema is OTel-aligned so exporting to OTLP
later is a mechanical mapper, not a redesign. The real design question was
'what goes in attributes' — and the answer is summaries, never payloads.
Payloads are a privacy and disk-time bomb; opt-in 4KB excerpts with mandatory
redaction cover the debugging case without making the trace store a data lake."

## Dependencies

- [[datasource-abstraction]] — Every DataSource emits spans
- [[orchestration]] — Engine is the heaviest span producer
- [[two-step-auth]] — Auth + auth_retry spans
- [[config-panel]] — `/api/history` gains execution drill-down links
- [[desktop-app]] — Renders the span tree in the execution detail view
- [[duckdb-cross-source]] — materialize + join spans (M4)
