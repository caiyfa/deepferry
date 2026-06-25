# Technical Design: Orchestration & Audit

> **Change ID**: `orchestration-audit` | **Status**: proposed

## Architecture Decisions

### 1. Orchestration is a DataSource subtype, not a layer above MCP

**Decision**: `OrchestratedDataSource` implements the same `DataSource` ABC as
MySQL/PostgreSQL/HTTP. The MCP `query()` tool is unaware of orchestration.

**Rationale**:
- Preserves the sacred ABC contract (`AGENTS.md` First Principle #2). No special
  casing in the MCP server or registry.
- An orchestrated source is *one* logical source from the agent's view. The
  agent says `query(source_id="db-platform", sql=...)`; the platform internally
  runs login→discover→route. The agent should not orchestrate.
- Keeps MCP stateless. All step state lives inside the source instance +
  SQLite trace store.

**Rejected Alternative**: A workflow engine sitting between MCP tools and
DataSources. This would create two invocation paths (direct vs orchestrated),
doubling test surface and breaking tool uniformity.

### 2. Declarative steps in TOML, not imperative Python

**Decision**: Orchestration flows are declared in `config.toml` as ordered
`[[sources.steps]]` blocks with `{{variable}}` interpolation. No user Python.

**Rationale**:
- Matches the project's declarative-config first principle. `config.toml` stays
  the single source of truth; flows are git-diffable and reviewable.
- Avoids the security & sandboxing nightmare of evaluating user Python.
- The interpolation engine is a small, auditable Jinja-subset (variable refs +
  index/key access only — no filters, no template inheritance).

**Rejected Alternative**: Python-defined flows (callables in a plugin dir).
Rejected for the same reason we chose TOML over YAML-as-code: declarative +
reviewable beats expressive + dangerous for a data-ferry tool.

### 3. Fan-out (foreach) but no branching or cycles

**Decision**: Steps support two topologies — **linear** (sequential, each step
sees prior outputs) and **foreach** (iterate one step over an array produced
upstream). No conditional `if`, no `while`, no DAG.

**Rationale**:
- The target use case (login → discover N instances → query) is exactly a
  linear-then-foreach shape. Branching adds combinatorial test/load with little
  payoff for a query tool.
- Conditionals in config become unmaintainable fast. If a flow needs logic, it
  belongs in a dedicated DataSource implementation (the ABC extension point),
  not in declarative steps.

**Rejected Alternative**: Full DAG with conditional edges. This is Airflow's
job, not deepferry's.

### 4. Reactive auth: single retry, then fail loud

**Decision**: On `401` (or configured `auth_failed_status`), the HTTP layer
invalidates the cached token, re-acquires it (Lock-guarded, so concurrent
calls still login once), and retries the original request **exactly once**. A
second 401 surfaces as `AUTH_FAILED`.

**Rationale**:
- TTL pre-emption cannot cover server-side revocation. Reactive 401 handling is
  mandatory for production platforms that rotate tokens on policy.
- Single retry bounds the blast radius. Infinite retry loops on a permanently
  broken auth endpoint would amplify load and mask real config errors.
- Reusing the existing `TokenManager` + `asyncio.Lock` means concurrent
  requests still deduplicate to one login — the invariant from
  `two-step-auth.md` acceptance criterion #6 is preserved.

**Rejected Alternative**: Exponential backoff with N retries. Rejected —
backoff policy is the caller's concern, not the ferry's. deepferry fails fast
with a structured error and lets the agent/operator decide.

### 5. Trace storage reuses SQLite `app.db`, span-shaped, OTel-aligned

**Decision**: Execution traces are stored as `query_executions` +
`execution_spans` tables in the existing runtime SQLite. Span schema mirrors
OpenTelemetry (trace_id, span_id, parent_span_id, kind, timestamps, attributes)
so future export to OTLP is mechanical.

**Rationale**:
- `AGENTS.md` First Principle: three-tier storage separation. Traces are
  runtime append-only metadata → SQLite `app.db`, exactly where query_history
  and token_cache already live. No new storage tier.
- OTel-shaped schema avoids a costly migration when (if) distributed tracing
  becomes a requirement. We pay nothing now (columns are cheap) and keep the
  export door open.
- Spans store **summaries** (durations, row counts, status, redacted headers),
  never raw payloads. Payload capture is opt-in per source for debugging.

**Rejected Alternative**: (a) Reuse `query_history` with a JSON blob column —
unqueryable, unindexed. (b) Add DuckDB for trace storage — DuckDB is the
analytics tier (M4), not runtime. (c) Embed a full OTel collector —
operations overhead disproportionate for a single-machine tool.

### 6. Desktop detail view is a timeline + expandable spans, not a flame graph

**Decision**: The execution detail page renders an ordered timeline of spans
(vertical list with start/duration/depth indentation) where each span row
expands to reveal input/output summaries, status, and redacted auth events.

**Rationale**:
- Flame graphs excel for profiling nested *latency*; deepferry's audience
  (analysts, ops) needs *provenance* — "which step, what input, what status".
  A span list answers that directly; a flame graph obscures it.
- AG Grid already handles the tabular result view. The timeline is a separate
  lightweight React component (no charting library), keeping bundle size
  within the Tauri 50-80MB budget.

**Rejected Alternative**: Flame graph (e.g. speedscope). Adds a dependency and
the wrong mental model for non-engineers.

## Component Interaction

```
Agent: query(source_id="db-platform", sql="SELECT ...")
   │
   ▼
MCP Server (unchanged) ── resolves source_id ──► OrchestratedDataSource
                                                   │
                                                   ▼
                                          OrchestrationEngine
                                          ├── opens trace root span
                                          ├── Step "login"
                                          │     ├── span(auth)
                                          │     ├── TokenManager.get_token()
                                          │     │     (401 path → invalidate → relogin)
                                          │     └── bind {{login.token}}
                                          ├── Step "discover" (foreach-capable)
                                          │     ├── span(http_request)
                                          │     └── bind {{discover.instances}}
                                          ├── Step "query"
                                          │     ├── span(http_request)
                                          │     └── returns StructuredResult
                                          └── closes trace root span
                                                   │
                                                   ▼
                                          TraceSink (SQLite app.db)
                                          ├── query_executions (1 row)
                                          └── execution_spans (N rows)
                                                   │
                                                   ▼
                                Desktop /api/executions/:id
                                          renders timeline + spans
```

## Data Flow: Orchestrated Query (happy + 401 path)

```
Happy path:
  Agent query → OrchestratedDataSource.execute()
    → Engine.run(steps)
      → Step "login":  cache HIT (token valid) → inject → skip HTTP
      → Step "discover": GET /instances (200) → [{id:"prod-1"}, ...]
      → Step "query":   POST /instances/prod-1/query (200) → rows
    → each step opens/closes a span; root span aggregates

401 reactive path:
  Agent query → OrchestratedDataSource.execute()
    → Step "query": POST .../query (401 Unauthorized)
      → HTTPInterceptor detects 401
      → TokenManager.invalidate(source_id)  # drop cache
      → TokenManager.get_token()            # Lock → re-login (once)
      → retry POST .../query (200)          # exactly ONE retry
      → span records: auth_refreshed=true, retry_count=1
    → if second 401: raise AUTH_FAILED (structured error, span status=error)
```

## Decisions Added by the Production-Hardening Scope Expansion

### 7. Cross-source aggregation is v1 core, not an M4 demo

**Decision**: `duckdb-cross-source.md` is rewritten and repositioned from M4
demo finale to a v1 core capability. The pandas fallback is removed; full
materialization is replaced by a three-tier strategy (lazy / paginated /
explicit).

**Rationale**: Aggregation-across-sources is the product's reason to exist.
The "30-second showcase" framing led to demo-grade compromises (unbounded
materialization, sampling-only schema, pandas escape hatch) that are production
footguns. The capability stays; the demo compromises go.

**Rejected Alternative**: Cut cross-source entirely for v1. Rejected — without
aggregation, deepferry degrades into N independent single-source tools and
loses its core value.

### 8. Filter pushdown is a startup-enforced red line

**Decision**: `mysql_enable_predicate_analyzer` / `postgres_scanner` pushdown
MUST be enabled. `enforce_pushdown = true` (default) refuses to start if any
attached source lacks pushdown. A query-plan pre-check rejects full-table
scans with `MISSING_PREDICATE`.

**Rationale**: `ATTACH` defaults to pulling entire source tables across the
wire. A naive cross-source JOIN against a 10M-row production table saturates
network + memory. This is the single most dangerous footgun; it is a hard
gate, not an optimization.

**Rejected Alternative**: "Run without pushdown" bypass flag. Rejected for
production — there is no safe way to run unfiltered.

### 9. Query safeguards are admission criteria, not optimizations

**Decision**: Read-only enforcement, statement timeout, row cap, streaming
cursor, pool ceiling, and dangerous-keyword block are mandatory on every SQL
path; response size cap + pagination on every HTTP path. Pass-through SQL is
declared intentional — safety comes from the three gates (read-only account +
keyword block + timeout/row cap), not from forbidding SQL text.

**Rationale**: These are the difference between a tool a DBA permits and one
they refuse. Without them, one runaway agent query can exhaust a source DB.
They are non-optional on any path that touches production data.

**Rejected Alternative**: "Optional safeguards via config flags." Rejected —
defaults must be safe; an operator who disables them is choosing risk
deliberately and must configure it explicitly.

### 10. Scenario correlation is two-layered (session + scenario), optional

**Decision**: Two correlation layers — `session_id` (MCP `Mcp-Session-Id`,
captured automatically) and `scenario_id` (agent-driven via
`start_scenario` / `end_scenario`). Both optional; unattributed queries still
trace under `session_id` alone.

**Rationale**: MCP is stateless, but agents investigate in multi-query logical
scenarios. Forcing every query to carry a `scenario_id` burdens the agent;
relying only on `session_id` is too coarse. Two layers give graceful
degradation — best-case grouping when the agent cooperates, session-level
grouping when it doesn't.

**Rejected Alternative**: (a) Force `scenario_id` on every query (agent
burden). (b) Infer scenarios from timestamp clustering (unreliable).
(c) Only `session_id` (too coarse — one session may span multiple
investigations).

### 11. No escape hatches: production posture over demo convenience

**Decision**: Demo escape hatches are deleted, not gated. The pandas
cross-source fallback is removed; full materialization is removed;
sampling-only schema is demoted to opt-in assistance. Failures surface as
structured errors.

**Rationale**: Escape hatches mask failures and produce "it kind of works"
results that are worse than honest errors in production. `project.md`
Production Posture codifies this as a durable commitment.

**Rejected Alternative**: Keep escape hatches behind flags. Rejected — flags
drift to "on" under deadline pressure; the durable answer is deletion.

## Data Flow: Cross-Source Federation (v1 core)

```
Agent: query(source_id="cross", sql=
  "SELECT c.name, SUM(o.amount) FROM mysql_src.customers c
   JOIN http_orders o ON c.id = o.user_id GROUP BY c.name LIMIT 10")
   │
   ▼
CrossSourceDataSource.execute()
   ├── opens join root span
   ├── EXPLAIN — query-plan pre-check
   │     └── reject if any attached source shows full-table scan (MISSING_PREDICATE)
   ├── ATTACH mysql_src (pushdown enforced at startup)
   ├── Materialize http_orders
   │     ├── strategy: lazy (default) | paginated | explicit
   │     ├── opens materialize span
   │     └── honors max_rows_per_source
   ├── DuckDB executes JOIN (memory_limit + temp spill to disk)
   ├── Result-set guard: LIMIT present? yes → proceed
   └── closes join root span (row_count, truncated, left_source, right_source)
   │
   ▼
StructuredResult (truncated=false) → logged to query_executions + scenario_id
```

## Open Questions

| # | Question | Default if unresolved |
|---|----------|----------------------|
| 1 | Should `foreach` results merge (UNION) or return as a list of result sets? | Merge by default (aligns with single-StructuredResult contract); list mode via `foreach_mode = "separate"` |
| 2 | Should trace retention auto-prune? | Yes — default 30-day TTL on `execution_spans`, configurable in `[audit]` |
| 3 | Should the agent see step-level errors or only the final one? | Final only by default (MCP stays simple); full trace exposed via desktop/API for humans |

These defaults are captured in the specs; revisit during M2.5 implementation.
