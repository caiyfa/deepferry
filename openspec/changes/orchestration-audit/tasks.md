# Implementation Tasks: Orchestration & Audit

> **Change ID**: `orchestration-audit` | **Baseline**: post-M2 (staged as M2.5)

---

## Phase 1: Reactive Auth (extends M2)

### Two-Step Auth — 401 Handling
- [ ] Add `TokenManager.invalidate(source_id: str) -> None`
  - Delete cached token row; release any in-flight refresh lock holder so the
    next `get_token()` re-logs in. Must be idempotent.
- [ ] Add `HTTP_AUTH_RETRY_STATUSES` config (default `{401}`) on HTTP source.
- [ ] Implement `RequestRunner._request_with_auth_retry()`:
  - Inject token → request → if status in retry set → invalidate → re-acquire
    (Lock-guarded) → retry **once** → if still failing, raise `AUTH_FAILED`.
- [ ] Audit: every retry opens a child span (`span_type="auth_retry"`,
  `attribute auth.refreshed=true`).
- [ ] Tests:
  - [ ] 401 on first call → re-login → retry succeeds (login called twice total)
  - [ ] 401 on both calls → AUTH_FAILED (login called twice, no third attempt)
  - [ ] 5 concurrent 401s → login called exactly twice (once initial, once
        recovery) — concurrency invariant preserved
  - [ ] TTL pre-emption path still works (regression)

---

## Phase 2: Orchestration Engine (M2.5 core)

### DataSource Abstraction Extension
- [ ] Add `OrchestratedDataSource(DataSource)` to `datasources/` with the same
      5 ABC methods. `execute()` runs the engine; `list_resources()` returns
      the **dynamic** post-discovery resource list (cached per-source with TTL).
- [ ] Add `DynamicDiscoveryMixin` contract: `async def discover() ->
      list[Resource]` — invoked by `list_resources()` for orchestrated sources.

### Orchestration Engine
- [ ] Define Pydantic models: `Step`, `StepBinding`, `OrchestrationConfig`,
      `ForeachConfig`.
- [ ] Implement `Interpolation` — Jinja-subset: `{{step_name.output_key}}`,
      `{{step_name.array[0].field}}`. Reject filters/includes; raise
      `INVALID_BINDING` on unknown refs.
- [ ] Implement `OrchestrationEngine.run(config, inputs) -> StructuredResult`:
  - [ ] Linear step execution with variable binding
  - [ ] `foreach` step: iterate array, execute sub-step per element, merge
        results (UNION by default; `separate` mode returns multi-dataset)
  - [ ] Per-step error → mark span `error`, raise `STEP_FAILED` with step name
        + upstream bindings for debugging
- [ ] Integrate with `TraceSink`: engine opens root span + one span per step.
- [ ] Config validation at startup: cycle detection (reject), undefined
      binding detection, required-field checks.
- [ ] Tests:
  - [ ] Linear 3-step flow end-to-end against mock server
  - [ ] foreach over 3 instances → merged result (3× rows)
  - [ ] Binding missing upstream → INVALID_BINDING at validation, not runtime
  - [ ] Mid-step 401 → reactive auth path (Phase 1) fires within the step

### Config Example
- [ ] Write `config.example.orchestrated.toml` showing a DB-query-platform
      scenario (login + discover + foreach query).
- [ ] Validate example loads without error in a smoke test.

---

## Phase 3: Execution Trace & Audit

### Trace Storage
- [ ] Add migration: `query_executions` + `execution_spans` tables to
      `app.db` (see `audit-trace.md` schema).
- [ ] Add retention job: prune spans older than `[audit].retention_days`
      (default 30) on server start + every 24h.
- [ ] Add payload sampler: opt-in via `[sources.X].trace.capture_payload =
      true` (default false). When true, store redacted request/response
      excerpts (max 4KB each).

### TraceSink API
- [ ] `TraceSink.start_execution(source_id, root_query_id) -> execution_id`
- [ ] `TraceSink.start_span(execution_id, parent_id, name, kind) -> span_id`
- [ ] `TraceSink.finish_span(span_id, status, summary)`
- [ ] All three async, append-only, concurrency-safe (single writer connection).

### Instrumentation
- [ ] Instrument `MySQLDataSource.execute` / `PostgreSQLDataSource.execute` —
      one span each (`kind="sql_exec"`).
- [ ] Instrument HTTP datasource request — one span (`kind="http_request"`).
- [ ] Instrument two-step auth login / refresh — one span (`kind="auth"`).
- [ ] Instrument DuckDB materialize + JOIN (M4) — one span per phase.
- [ ] Tests:
  - [ ] A single SQL query produces exactly 1 execution + 1 span
  - [ ] An orchestrated query produces 1 execution + N spans with correct
        parent linkage
  - [ ] Span ordering matches execution order (verify via created_at + depth)

### Query API
- [ ] `GET /api/executions?source_id=&status=&limit=` — paginated list
- [ ] `GET /api/executions/:id` — execution + nested spans tree
- [ ] `GET /api/executions/:id/spans/:span_id` — single span detail

---

## Phase 4: Desktop Execution Detail View

### Frontend
- [ ] Add route `/history/:id` → `ExecutionDetail` component
- [ ] `ExecutionTimeline`: vertical ordered span list with depth indentation,
      color-coded status (green success / red error / amber auth-refresh)
- [ ] `SpanRow`: expandable — reveals kind, duration, input/output summary,
      auth-refresh flag, redacted headers
- [ ] `SpanFilters`: filter by kind / status; collapse all / expand all
- [ ] Link from existing `/history` row → `/history/:id`
- [ ] Tests:
  - [ ] Timeline renders N spans in execution order
  - [ ] Expand span → shows summary fields
  - [ ] Auth-refresh span visually distinct (amber)
  - [ ] Large trace (50 spans) renders <500ms

---

## Phase 5: Cross-Source Aggregation Rewrite (v1 core)

### DuckDB Federation Hardening
- [ ] Engine init with mandatory PRAGMAs (`memory_limit`, `temp_directory_path`,
      `max_temp_directory_size`); refuse to start if `memory_limit` unset.
- [ ] Enable filter pushdown at startup (`mysql_enable_predicate_analyzer`,
      `mysql_aggregate_pushdown_enabled`, PG equivalents);
      `enforce_pushdown = true` refuses start if any source lacks pushdown.
- [ ] Query-plan pre-check: `EXPLAIN` every cross-source statement; reject
      full-table scans with `MISSING_PREDICATE` + a fix suggestion.
- [ ] Three-tier materialization: lazy (default) / paginated / explicit;
      honor `max_rows_per_source`; `truncated=true` on cap.
- [ ] Result-set guarding: reject non-aggregated SELECTs without `LIMIT`
      (`MISSING_LIMIT`); inject `FETCH FIRST max_join_output_rows` fallback.
- [ ] Explicit-schema-preferred relation creation; `SCHEMA_DRIFT` warning on
      response/declaration mismatch (no silent VARCHAR coercion).
- [ ] **Remove pandas fallback entirely** (grep-enforced: no Python-level JOIN).
- [ ] Credential safety: ATTACH strings built in-memory, never logged; spans
      record source ids only; driver errors scrubbed of credential substrings.
- [ ] Tests:
  - [ ] Pushdown disabled at startup → `PUSHDOWN_DISABLED` (no bypass).
  - [ ] EXPLAIN detects full-table scan → `MISSING_PREDICATE`.
  - [ ] JOIN exceeding `memory_limit` spills to disk, completes (no OOM).
  - [ ] HTTP relation >`max_rows_per_source` truncated + flagged.
  - [ ] SELECT without LIMIT → `MISSING_LIMIT`.
  - [ ] Schema drift → warning span, no silent coercion.
  - [ ] Grep confirms zero pandas/Python-Join code paths.

---

## Phase 6: Data Source Production Safeguards

### SQL Sources (MySQL + PostgreSQL)
- [ ] Read-only enforcement (MySQL read-only account + `SET TRANSACTION READ
      ONLY`; PG `SET default_transaction_read_only = on`).
- [ ] Per-query statement timeout (`MAX_EXECUTION_TIME` / `statement_timeout`).
- [ ] Dangerous-keyword pre-scan (`DROP/TRUNCATE/DELETE/UPDATE/INSERT/ALTER/
      GRANT/REPLACE/MERGE`) → `WRITE_BLOCKED`.
- [ ] Row cap injection (`LIMIT` if absent, capped by `max_rows`); `truncated`.
- [ ] Streaming cursor (`fetchmany` loop); grep-enforce no `fetchall`.
- [ ] Pool ceiling (`max_pool_size`) + per-source concurrency `Semaphore`.
- [ ] Tests: DROP rejected, timeout kills at DB, row truncation, concurrency
      quota honored, grep confirms no fetchall.

### HTTP API Source
- [ ] Response size cap (`max_response_bytes`, default 50MB) → `RESPONSE_TOO_LARGE`.
- [ ] Flatten row cap (`max_rows`); `truncated=true`.
- [ ] Explicit `columns` schema declaration; reject mismatched fields.
- [ ] Pagination support (`?offset=` / `?cursor=`) up to `max_rows`.
- [ ] Per-request timeout from `QueryRequest.timeout`.
- [ ] Status-code mapping (4xx `HTTP_CLIENT_ERROR`, 5xx `HTTP_SERVER_ERROR`,
      401 → reactive auth path).
- [ ] Tests: oversized response rejected, truncation, schema mismatch rejected.

---

## Phase 7: Scenario Correlation & MCP Tools

### MCP Server
- [ ] Add `list_sources` tool → `[SourceSummary]` (id/name/type/health).
- [ ] Add `start_scenario(label?)` → `{scenario_id}` and `end_scenario(id)`.
- [ ] Extend `query` with `scenario_id?` and `max_rows?` params.
- [ ] Capture MCP `Mcp-Session-Id` → `session_id` on every call (auto).
- [ ] Tests: `list_sources` returns all; scenario open/close; session captured.

### Audit Trace — Scenario
- [ ] `query_executions`: add `session_id`, `scenario_id` columns + indexes.
- [ ] `query_scenarios` table + indexes.
- [ ] `TraceSink.start_scenario` / `attach_to_scenario` / `end_scenario`.
- [ ] Query API: `GET /api/scenarios`, `GET /api/scenarios/:id`,
      `POST /api/scenarios`, `POST /api/scenarios/:id/close`.
- [ ] Tests: scenario groups its queries; unattributed queries trace under
      `session_id` alone; reopen is idempotent.

### Desktop — Scenario Views
- [ ] `/scenarios` route → `ScenarioList` (counts + drill-down).
- [ ] `/scenarios/:id` route → `ScenarioDetail` (grouped queries timeline +
      multi-tab result comparison).
- [ ] Link `/history` rows → their scenario when `scenario_id` present.
- [ ] Tests: scenario lists render; drill-down shows all grouped queries.

---

## Phase 8: Project-Level Repositioning

- [ ] `project.md`: reposition M4 cross-source as v1 core; insert M2.5 row.
- [ ] Add "v1 Production Posture" section to `project.md`.
- [ ] Verify all `[[wikilinks]]` resolve after milestone reshuffle.
- [ ] Update `config.example*` files for new keys (`max_rows`, `max_pool_size`,
      `max_response_bytes`, `enforce_pushdown`, DuckDB PRAGMAs, `[audit]`).
- [ ] Remove "30-second demo video" and "wow factor" tasks from any task list.

---

## Definition of Done

- [ ] `two-step-auth.md` acceptance criteria + new 401 criteria all pass
- [ ] `orchestration.md` acceptance criteria pass (end-to-end orchestrated query)
- [ ] `audit-trace.md` acceptance criteria pass (trace + scenario correlation)
- [ ] `desktop-app.md` new acceptance criteria pass (execution detail + scenarios)
- [ ] `duckdb-cross-source.md` acceptance criteria pass (production-grade federation)
- [ ] `sql-datasource.md` + `http-api-datasource.md` safeguard criteria pass
- [ ] `mcp-server.md` criteria pass (6 tools + scenario correlation)
- [ ] `ruff check .` + `mypy --strict` clean
- [ ] Core logic coverage >70% on new modules
- [ ] `config.example*` files load without error
- [ ] Grep confirms: no pandas fallback, no `fetchall`, no demo-video tasks

- [ ] `two-step-auth.md` acceptance criteria + new 401 criteria all pass
- [ ] `orchestration.md` acceptance criteria pass (end-to-end orchestrated query)
- [ ] `audit-trace.md` acceptance criteria pass (trace produced + queryable)
- [ ] `desktop-app.md` new acceptance criteria pass (detail view renders)
- [ ] `ruff check .` + `mypy --strict` clean
- [ ] Core logic coverage >70% on new modules
- [ ] `config.example.orchestrated.toml` verified loadable
