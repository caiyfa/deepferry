# Change Proposal: Orchestration, Audit & Production Hardening

> **Change ID**: `orchestration-audit` *(scope expanded 2026-06-25)*
> **Status**: proposed
> **Created**: 2026-06-25
> **Depends on**: `mvp-init` (M1–M4 baseline)
> **Timeline**: Spans M2.5 (orchestration + audit + scenario) and the M4 repositioning (cross-source aggregation promoted to v1 core)

## Why

The `mvp-init` baseline treats each data source as an independent, static,
atomic resource and frames several capabilities as portfolio demos. Real
production data platforms and production-grade operations do not fit either
assumption. This change closes **eight gaps**.

### Original four gaps (orchestration & audit)

1. **Login-gated multi-step flows** — Enterprise DB query platforms require
   `login → discover account-permitted instances → route SQL to a chosen
   instance`. The single-request `execute()` model cannot express request
   chaining where one step's output feeds the next step's input.
2. **Dynamic resource discovery** — `list_resources()` for HTTP sources only
   returns config-declared endpoints. Account-permitted instances are only
   knowable *after* login; static TOML cannot enumerate them.
3. **Reactive auth recovery** — `two-step-auth` only refreshes tokens via TTL
   pre-emption. There is no path for `401` responses (server-side revocation,
   policy rotation). A 401 becomes a terminal `AUTH_FAILED` instead of
   triggering re-login.
4. **Black-box execution** — `query_history` records only the final outcome of
   a single flat query. Multi-step orchestrated queries, cross-source joins,
   and token-refresh events leave zero trace. Operations and compliance cannot
   audit *how* a result was produced — only *that* a query ran.

### Four gaps added by the scope expansion (production hardening)

5. **Cross-source aggregation was framed as a demo** — `duckdb-cross-source.md`
   described itself as the "wow factor demo for the 30-second showcase video"
   with full materialization and a pandas fallback. Aggregation-across-sources
   is the product's reason to exist (operational reports, reconciliation,
   customer-360), not a demo. The demo escape hatches (pandas fallback,
   unbounded full materialization, sampling-only schema) are production
   footguns: an unfiltered `ATTACH` pulls a whole production table across the
   wire and OOMs the host.
6. **No query safeguards on any data path** — Agent-supplied SQL executes
   against production databases with no read-only enforcement, no statement
   timeout, no row cap, no streaming, no pool ceiling. A single runaway query
   can exhaust a source DB. This is the difference between a tool a DBA permits
   and one they refuse.
7. **No scenario correlation** — MCP is stateless, and an agent's multi-query
   investigation (e.g., "analyze Q3 refund anomaly") leaves no logical grouping.
   A reviewer must infer related queries from timestamps. There is no
   session/scenario primitive to aggregate "all queries for this investigation."
8. **No agent discovery surface** — The MCP server exposes only
   `list_tables` / `query` / `schema_info`. An agent cannot discover *which*
   sources exist (`list_sources`), nor open/close a logical scenario. The
   agent has no first call to make.

**deepferry** must close all eight to integrate real production platforms,
serve agent-driven cross-source analysis, and make every query auditable rather
than a black box. The first version ships **fewer features, each
production-grade** — not a demo.

## What Changes

### ADDED Capabilities

| Capability | Spec | Baseline |
|-----------|------|----------|
| Orchestration Engine | `specs/orchestration.md` | NEW |
| Execution Trace & Audit | `specs/audit-trace.md` | NEW (incl. scenario correlation) |

### MODIFIED Capabilities

| Capability | Spec | Change |
|-----------|------|--------|
| DataSource Abstraction | `specs/datasource-abstraction.md` | Add `OrchestratedDataSource` subtype + dynamic-vs-static discovery contract |
| Two-Step Authentication | `specs/two-step-auth.md` | Add reactive `401` detection → invalidate → single retry path |
| MCP Server | `specs/mcp-server.md` | Add `list_sources` / `start_scenario` / `end_scenario` tools; extend `query` with `scenario_id?` / `max_rows?`; add Scenario Correlation section |
| SQL Data Sources | `specs/sql-datasource.md` | Add Production Safeguards (read-only, timeout, row cap, streaming, pool ceiling, dangerous-keyword block, SQL-injection stance) |
| HTTP API Data Source | `specs/http-api-datasource.md` | Add Production Safeguards (response size cap, pagination, explicit-schema-preferred, status mapping) |
| Cross-Source Aggregation | `specs/duckdb-cross-source.md` | **Full rewrite**: repositioned from M4 demo to v1 core; mandatory filter pushdown; three-tier materialization; memory bounding + disk spill; explicit schema; result-set guarding; **pandas fallback removed** |
| Desktop App | `specs/desktop-app.md` | Add execution detail view (timeline + spans) **and** `/scenarios` + `/scenarios/:id` scenario-aggregation views |
| Project Overview | `project.md` | Reposition cross-source aggregation as v1 core; insert M2.5; add "v1 Production Posture" declaration |

### Out of Scope (This Change)

- **Full workflow DAG engine** — Only linear and fan-out (foreach) step graphs.
  No cycles, no long-running async jobs, no human-in-the-loop pause/resume.
  deepferry is a query ferry, not Airflow.
- **Distributed tracing backend** — Traces persist in the existing SQLite
  `app.db`. No Jaeger/Tempo/OpenTelemetry collector. The span model is
  OTel-shaped for future export, but storage stays local.
- **NL2SQL / intent routing** — Step selection is declarative or agent-driven,
  not LLM-inferred.
- **Write-path / mutation support** — deepferry is read-only by design. The
  dangerous-keyword block enforces this; no `INSERT/UPDATE/DELETE` path is
  added, ever.
- **High-QPS OLTP** — DuckDB federation is for analytical, agent-scale
  workloads. Not a replacement for a transactional DB driver.

## Risks

| Risk | Mitigation |
|------|------------|
| Orchestration scope-creeps into a generic workflow tool | Hard cap: linear + foreach only. Reject cycle/branch requests; route them to a real orchestrator. |
| Span volume explodes for large result sets | Spans store summaries only (row counts, durations), never full payloads. Payload sampling opt-in, ≤4KB, redacted. |
| 401 retry storm if server is permanently broken | Single retry, then surface `AUTH_FAILED`. Exponential backoff explicitly out of scope. |
| Cross-source JOIN pulls a whole production table (filter pushdown off) | `enforce_pushdown = true` at startup refuses to run without pushdown; query-plan pre-check rejects full-table scans with `MISSING_PREDICATE`. |
| Cross-source JOIN output explodes (cartesian) | `MISSING_LIMIT` rejection for non-aggregated SELECTs; `FETCH FIRST N` fallback at `max_join_output_rows`. |
| Agent forgets `scenario_id` and queries go ungrouped | Scenario correlation is optional; unattributed queries still trace under `session_id` (MCP `Mcp-Session-Id`). Grouping degrades gracefully, does not fail. |
| Production-grade scope slips back to demo habits | `project.md` Production Posture is the durable commitment; "no escape hatches" is an acceptance criterion. |

## Dependencies

- [[datasource-abstraction]] — Adds subtype + dynamic discovery contract
- [[two-step-auth]] — Extends token lifecycle with reactive invalidation
- [[http-api-datasource]] — Orchestration wraps HTTP steps; gains safeguards
- [[sql-datasource]] — Gains production safeguards; becomes ATTACH target
- [[duckdb-cross-source]] — Full rewrite: the cross-source aggregation engine
- [[orchestration]] — New; the multi-step engine
- [[audit-trace]] — New; instruments everything + scenario correlation
- [[mcp-server]] — New tools + scenario correlation contract
- [[desktop-app]] — Execution detail + scenario views consume trace data
