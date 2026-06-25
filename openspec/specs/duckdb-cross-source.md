# Capability: Cross-Source Aggregation

> **Status**: planned | **Milestone**: v1 core (repositioned from M4 demo) | **Owner**: backend | **Differentiator**: ⭐

## Summary

DuckDB as an embedded federation engine: agent issues one SQL statement that
JOINs, UNIONs, or aggregates data spread across MySQL, PostgreSQL, HTTP APIs,
and login-gated query platforms — and receives one combined, report-shaped
result. This is deepferry's core value proposition (the "ferry" moves data
between sources *and recombines it*), not a demo showcase.

## Motivation

- **Aggregation across sources IS the product.** An agent answering "top
  customers by order value" where customers live in MySQL and orders come from
  a login-gated HTTP API cannot answer the question by querying each source
  separately — the answer requires a JOIN. Without cross-source aggregation,
  deepferry degrades into N independent single-source tools.
- Operational reports, cross-system reconciliation, and customer-360 views are
  the dominant enterprise data-consumption pattern. BI tools (Superset,
  Metabase) exist precisely for this. deepferry must serve the same need for
  agent-driven ad-hoc analysis.
- **DuckDB is the correct engine for single-machine, embedded federation** — no
  server to run (unlike Trino/Presto, which are out of scope as too heavy),
  native `mysql_scanner` / `postgres_scanner` extensions, a fast C++ columnar
  engine. The choice stands; the *implementation* in the prior spec revision
  was demo-grade and is rewritten here.

### What changed from the prior revision

The previous spec framed this capability as the "wow factor demo for the
30-second showcase video" with full materialization and a pandas fallback. That
framing is rejected. Cross-source aggregation is a first-class production
capability. The demo-oriented escape hatches (pandas fallback, unbounded full
materialization, sampling-only schema inference) are removed and replaced with
production hardening.

## Specification

### Architecture: Federation, Not ETL

deepferry does **not** copy data into a warehouse. DuckDB runs in-process and
federates queries at execution time:

```
Agent: "Top 10 customers by total order value"
  (customers in MySQL, orders from a two-step-auth HTTP API)
        │
        ▼
   DuckDB Engine (embedded, in-process)
   ┌─────────────────────────────────────────┐
   │  SELECT c.name, SUM(o.amount) AS total   │
   │  FROM mysql_src.customers c              │
   │  JOIN http_orders o ON c.id = o.user_id  │
   │  GROUP BY c.name ORDER BY total DESC 10  │
   └─────────────────────────────────────────┘
        │                          │
        ▼                          ▼
  mysql_scanner             HTTP source materialized
  (ATTACH, pushdown)        as DuckDB relation
                             (lazy / paginated / explicit)
```

Two kinds of source-side relations feed DuckDB:

| Relation type | Source | How DuckDB sees it |
|---------------|--------|--------------------|
| **Attached** | MySQL / PostgreSQL | `ATTACH '...' AS mysql_src (TYPE mysql)` — scanned live via extension, predicates pushed down |
| **Materialized** | HTTP API / orchestrated platform | JSON response registered as a DuckDB relation (view or temp table) per strategy below |

### Filter Pushdown — Mandatory (Production Red Line)

`mysql_scanner` / `postgres_scanner` are enhanced Foreign Data Wrappers. **By
default they scan entire source tables across the wire** before DuckDB applies
local filters. A naive cross-source JOIN against a 10M-row production table
will pull the whole table into deepferry and saturate the network + memory.

This is the single most dangerous production footgun. It is treated as a hard
gate, not an optimization:

```sql
-- MySQL scanner — MUST be enabled at startup
SET mysql_enable_predicate_analyzer = true;       -- (legacy: mysql_experimental_filter_pushdown)
SET mysql_aggregate_pushdown_enabled = true;       -- pushes COUNT/SUM to MySQL

-- PostgreSQL scanner — corresponding pushdown settings enabled
```

**Startup enforcement**: if any attached SQL source has pushdown disabled (or
the installed scanner version does not support predicate pushdown), the server
refuses to start with `PUSHDOWN_DISABLED`. There is no "run without pushdown"
escape hatch in production — it is the difference between a 50ms query and a
production incident.

**Query-plan pre-check**: before executing a cross-source statement, the engine
runs `EXPLAIN` and inspects for full-table scans on attached sources. If a
source-side table scan with no pushed predicate is detected, the query is
rejected with `MISSING_PREDICATE` and a suggestion to add a `WHERE` filter (or
a time / primary-key range bound). The agent (or operator) must narrow the
scan; deepferry does not silently pull whole tables.

**Known pushdown limits** (documented, not hidden): scanner pushdown covers
simple `WHERE` predicates, projections, and basic aggregates (`COUNT`, `SUM`,
`MIN`, `MAX`). Cross-source JOINs, window functions, and UDFs are **not**
pushed down — they execute in DuckDB after the (predicate-narrowed) data is
fetched. This is acceptable; the point is to never fetch unfiltered tables.

### Materialization Strategy — Three Tiers

The prior spec's "full materialization (simplest, sufficient for demo)" is
removed. HTTP-sourced relations use one of three strategies, chosen per
resource:

| Strategy | When | How | Memory profile |
|----------|------|-----|----------------|
| **Lazy fetch-on-join** (default) | DuckDB needs the relation only for a specific JOIN | Register as a view over `read_json_auto` / a scanning function; DuckDB fetches rows as the JOIN iterates, bounded by the join's own cardinality | Bounded by JOIN output, not source size |
| **Paginated materialization** | HTTP response known to be large; endpoint supports pagination/limits | Fetch page-by-page into a temp table, honoring `max_rows_per_source`; stop at cap and mark `truncated=true` | Bounded by `max_rows_per_source` |
| **Explicit materialization** | Operator declares a resource should be cached | `[[sources.resources.X]] materialize = true` with optional `refresh_interval`; engine creates a physical table + background refresh | Bounded by disk; refreshed async |

`max_rows_per_source` (default 100,000, configurable per source) is a hard cap.
Exceeding it truncates and flags the result — never an unbounded fetch.

### Memory Control — Hard Boundaries

DuckDB must not OOM the host. Mandatory PRAGMAs set at engine init, all
configurable via env-overridable defaults:

```sql
PRAGMA memory_limit = '${DUCKDB_MEMORY_LIMIT:2GB}';
PRAGMA temp_directory_path = '${DUCKDB_TEMP_DIR:/var/tmp/deepferry}';
PRAGMA max_temp_directory_size = '${DUCKDB_MAX_TEMP:10GB}';
```

Effect: when a JOIN/hash-aggregate exceeds `memory_limit`, DuckDB **spills to
disk** in `temp_directory_path` (slower, does not crash) rather than OOMing.
`max_temp_directory_size` bounds the spill so a runaway query fills disk
predictably and fails with `TEMP_SIZE_EXCEEDED` instead of taking the host
down.

These are not optional knobs — they are init-time invariants. The engine
refuses to start if `memory_limit` is unset.

### Schema Handling — Explicit Declared, Sampling Assisted

Sampling one HTTP response to infer schema is unstable: APIs drop nullable
fields, change key order, and drift across deployments. The production model
inverts the priority:

1. **Explicit declaration is the default.** Each HTTP resource declares its
   columns in `config.toml`:
   ```toml
   [[sources.resources]]
   name = "orders"
   path = "/orders"
   columns = [
     { name = "order_id", type = "BIGINT", nullable = false },
     { name = "user_id",  type = "BIGINT" },
     { name = "amount",   type = "DOUBLE" },
     { name = "status",   type = "VARCHAR" },
   ]
   ```
   DuckDB creates the relation with this exact schema; mismatched response
   fields are caught as errors, not silently coerced.

2. **Sampling is opt-in assistance only.** When `columns` is omitted, the
   engine uses `read_json_auto(..., strict)` against a bounded sample and
   records a `schema_inferred` warning. Fields absent from the sample but
   present in later responses surface as `SCHEMA_DRIFT` (warning span) rather
   than silent VARCHAR fallback. Production deployments should migrate inferred
   schemas to explicit declarations.

3. **Nullable / missing fields**: declared nullable columns stay typed (a
   missing `amount` is `NULL::DOUBLE`, not `'NULL'::VARCHAR`). No silent
   widening to VARCHAR.

### Result-Set Guarding

Cross-source JOIN output can explode (cartesian products, fan-out joins). The
output itself is guarded, not just the inputs:

- A cross-source `SELECT` without `LIMIT` or a terminal aggregation
  (`GROUP BY` / `SUM` / `COUNT`) is **rejected** with `MISSING_LIMIT` and a
  suggestion to add `LIMIT N` or aggregate.
- A server-side fallback injects `FETCH FIRST <max_join_output_rows> ROWS ONLY`
  (default 1,000,000, configurable) so even a query that nominally satisfies
  the rule cannot return an unbounded rowset.
- Truncation is reported in the `StructuredResult` via a `truncated: true` flag
  and a matching `result_truncated` span attribute.

### Concurrency Model

DuckDB is embedded and shared across queries. The model:

| Operation | Concurrency | Mechanism |
|-----------|-------------|-----------|
| Read queries (`SELECT` / cross-source JOIN) | Concurrent, multi-connection | DuckDB supports concurrent readers; each query takes its own connection from the pool |
| Materialization writes (`CREATE TABLE AS` / refresh) | Serialized | A single writer connection guarded by an `asyncio.Lock`; readers see the previous version until commit |
| `ATTACH` of a new source | Serialized | Briefly blocks the writer connection; readers unaffected |

Throughput ceiling: bounded by `DUCKDB_MEMORY_LIMIT` and source-side latency,
not by DuckDB itself for typical agent-scale (low QPS, analytical) workloads.
This is explicitly **not** a high-QPS OLTP engine.

### Integration with Trace

Every cross-source query is fully instrumented (see [[audit-trace]]):

- One `join` root span per cross-source `execute()`, carrying `left_source`,
  `right_source`, `row_count`, `truncated`.
- One `materialize` child span per HTTP-side relation that gets materialized
  (paginated or explicit), carrying `endpoint_name`, `row_count`, `strategy`.
- `schema_drift` and `result_truncated` are recorded as span attributes for
  audit. A query that triggered pushdown rejection leaves a `join` span with
  `status=error` and the rejection reason, so operators see *why* a query was
  refused, not just that it failed.

### Credential Safety

`ATTACH` strings contain source credentials. They must never appear in spans,
logs, or error messages:

- ATTACH strings are constructed in-memory from config + env vars; never logged.
- The `join` span records `left_source` / `right_source` as source **ids**, not
  connection strings.
- Errors from a scanner surface as `SOURCE_UNAVAILABLE` with the source id only;
  the underlying driver error is scrubbed of credential substrings before any
  attribute is written.

## Configuration

```toml
[duckdb]
memory_limit = "${DUCKDB_MEMORY_LIMIT:2GB}"
temp_directory = "${DUCKDB_TEMP_DIR:/var/tmp/deepferry}"
max_temp_size = "${DUCKDB_MAX_TEMP:10GB}"
max_rows_per_source = 100000          # per HTTP relation fetch cap
max_join_output_rows = 1000000        # cross-source output cap
enforce_pushdown = true               # refuse to start if any source lacks pushdown (default true, do not disable in prod)

[[sources]]
id = "prod-mysql"
type = "mysql"
# ... connection fields; ATTACHed automatically when referenced in a cross-source query

[[sources]]
id = "orders-api"
type = "orchestrated"                 # login-gated platform, see [[orchestration]]
# ...

  [[sources.resources]]
  name = "orders"
  columns = [                          # explicit declaration (production default)
    { name = "order_id", type = "BIGINT", nullable = false },
    { name = "user_id",  type = "BIGINT" },
    { name = "amount",   type = "DOUBLE" },
  ]
  materialize = "lazy"                 # "lazy" (default) | "paginated" | "explicit"
```

## Acceptance Criteria

1. A cross-source JOIN between a MySQL table and a two-step-auth HTTP API
   returns correct combined results for the demo scenario, end-to-end from a
   single agent `query()` call.
2. **Pushdown enforcement**: a source whose scanner lacks predicate pushdown
   causes startup to fail with `PUSHDOWN_DISABLED` (no bypass flag in prod).
3. **Predicate pre-check**: a cross-source `SELECT` that would full-table-scan
   an attached source is rejected with `MISSING_PREDICATE` and a fix
   suggestion; `EXPLAIN` confirms the rejection path.
4. **Memory bounding**: a JOIN exceeding `memory_limit` spills to
   `temp_directory` and completes (slowly) rather than OOMing; a spill
   exceeding `max_temp_directory_size` fails with `TEMP_SIZE_EXCEEDED`.
5. **Source cap**: an HTTP relation exceeding `max_rows_per_source` is
   truncated, with `truncated=true` in the result and a `result_truncated` span.
6. **Output cap**: a cross-source `SELECT` without `LIMIT`/aggregation is
   rejected with `MISSING_LIMIT`; the `FETCH FIRST N` fallback caps any query
   at `max_join_output_rows`.
7. **Schema drift**: an HTTP response containing fields absent from the declared
   schema raises `SCHEMA_DRIFT` (warning span), not silent VARCHAR coercion.
8. **No pandas fallback**: there is no Python-level JOIN code path. Federation
   failure surfaces as a structured error (the pandas escape hatch is removed).
9. **Trace**: every cross-source query produces a `join` root span + one
   `materialize` span per HTTP relation, queryable via `/api/executions/:id`.
10. **Credential safety**: grep across stored spans/logs for any source password
    or ATTACH credential substring returns zero matches.
11. `ruff check .` and `mypy --strict` pass; cross-source core coverage >70%.

## Interview Story

"The hardest call on this feature was not technical — it was refusing to ship
the demo version. The first spec revision treated cross-source JOIN as a
portfolio showpiece: full materialization, pandas fallback, schema by sampling.
That works for a screenshot and explodes in production — an unfiltered ATTACH
pulls a whole MySQL table across the wire and OOMs the host. The rewrite makes
three things non-negotiable: filter pushdown is enforced at startup (refuse to
run without it), memory is bounded with disk-spill (never OOM), and the pandas
escape hatch is deleted so failures surface instead of being masked. The
interesting engineering is the query-plan pre-check — we EXPLAIN every
cross-source statement and reject full-table scans before they touch the
network. Federation is easy to demo and hard to operate; the spec now reflects
the operating part."

## Dependencies

- [[sql-datasource]] — MySQL / PostgreSQL sources become ATTACH targets
- [[http-api-datasource]] — HTTP responses become materialized relations
- [[orchestration]] — Login-gated platforms feed relations through orchestration steps
- [[two-step-auth]] — Auth for protected HTTP-side relations
- [[audit-trace]] — `join` / `materialize` / `schema_drift` spans
- [[datasource-abstraction]] — Cross-source execute() returns the same StructuredResult
