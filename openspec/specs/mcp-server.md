# Capability: MCP Server

> **Status**: planned | **Milestone**: M1 | **Owner**: backend

## Summary

Implement an MCP-compliant server exposing data source query capabilities as `tools` to LLM agents (Claude Desktop, etc.). Supports both `stdio` and `Streamable HTTP` transport.

## Motivation

- LLM agents need a standardized protocol to discover and execute data queries
- MCP is the emerging standard for agent-tool communication
- Must support at least 3 tools: `list_tables`, `query`, `schema_info`

## Specification

### Transport Protocols

| Protocol | Use Case |
|----------|----------|
| `stdio` | Claude Desktop local integration |
| Streamable HTTP | Remote agent access, Tauri sidecar |

### Tools Exposed

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `list_sources` | — | `[SourceSummary]` | List all configured sources (id/name/type/health). The agent's first call to discover what is queryable |
| `list_tables` | `source_id: str` | `[TableInfo]` | List all tables/views/resources in a data source |
| `schema_info` | `source_id: str, table?: str` | `SchemaInfo` | Get schema metadata for tables |
| `query` | `source_id: str, sql: str, params?: dict, scenario_id?: str, max_rows?: int` | `QueryResult` | Execute a query; `scenario_id` correlates it into a logical scenario (see Scenario Correlation); `max_rows` caps output |
| `start_scenario` | `label?: str` | `{scenario_id: str}` | Open a named investigation scenario; subsequent `query` calls carrying the returned id aggregate under it for review |
| `end_scenario` | `scenario_id: str` | `{scenario_id: str, query_count: int}` | Close a scenario; it becomes read-only in the trace store |
| `propose_source` | `type: str, host: str, port?: int, database?: str, name?: str` | `{source_id: str, status: "pending"}` | **Desktop mode only.** Agent proposes a new source topology (no credentials). Lands as a pending entry the user confirms in the desktop UI; on confirm, credentials go to the OS keychain and the source becomes active. See § Operating Modes. |

### Scenario Correlation

MCP is stateless by design, but agents investigate in multi-query logical
scenarios ("analyze Q3 refund anomaly"). deepferry correlates queries at two
layers, both surfaced in [[audit-trace]]:

| Layer | Granularity | Source | Injection |
|-------|-------------|--------|-----------|
| `session_id` | One agent connection | MCP `Mcp-Session-Id` header (Streamable HTTP transport) | Captured automatically; agent-unaware |
| `scenario_id` | One investigation task | Agent-driven | `start_scenario` returns an id; agent passes it to subsequent `query` calls |

`scenario_id` is optional — unattributed queries still execute and trace under
their `session_id` alone. `start_scenario` / `end_scenario` exist so a human
reviewing the desktop `/scenarios` view can group "all queries for this
investigation" without inferring from timestamps.

### Multi-Turn Agent Orchestration (Agent-as-Orchestrator)

MCP tools are designed for the agent to **drive**. While the server-side
`OrchestratedDataSource` (see [[orchestration]]) covers fixed, HTTP-only
multi-step flows, any flow that **mixes source types** (HTTP → DB → HTTP) or
needs **conditional logic** is expressed by the agent itself across multiple
`query()` calls. This is the canonical MCP usage — the standard
`while stop_reason == "tool_use"` agent loop — and is a first-class supported
pattern, not an escape hatch.

```
Agent opens scenario → start_scenario("cust+orders") → scen_id
  │
  ├─ query(source_id="db-platform",                       # orchestrated HTTP source
  │        sql="SELECT cust_id,region FROM ...",
  │        scenario_id=scen_id)
  │     → StructuredResult { cust_id, region, ... }       # base info
  │
  ├─ query(source_id="prod-mysql",                        # direct SQL source
  │        sql="SELECT order_id,amount FROM orders
  │               WHERE cust_id IN :ids",
  │        params={"ids": [...]},                         # ← prior result injected
  │        scenario_id=scen_id)
  │     → StructuredResult { order_id, amount, ... }
  │
  └─ query(source_id="logistics-api",                     # another HTTP source
           statement="shipments",
           params={"order_id": "O-5001"},                 # ← prior result injected
           scenario_id=scen_id)
        → StructuredResult { shipment_status, ... }

All three calls group under scen_id in the desktop /scenarios view for audit.
```

**Cross-call data passing contract.** The `query` tool's `params: dict` is the
universal carrier between turns. Its binding is source-type dependent and
**must** be honored by every `DataSource`:

| Source type | `params` binds to | Example |
|-------------|-------------------|---------|
| SQL (MySQL/PG) | Named SQL parameters (`:name`) in `statement` | `WHERE id IN :ids` ← `params={"ids":[..]}` |
| HTTP (GET) | URL query string | `?order_id=O-5001` |
| HTTP (POST) | `body_template` `{{var}}` interpolation **from `params`** | see [[http-api-datasource]] § Query Parameters |
| Orchestrated | `{{params.x}}` in step `path`/`headers`/`body_template` | step interpolation scope |

**When to use agent orchestration vs. alternatives:**

| Flow shape | Recommended path |
|------------|------------------|
| Dynamic / needs conditional logic / mixes source types (HTTP↔DB) | **Agent orchestration** (this section) |
| Fixed HTTP-only multi-step (login → discover → query) | Server-side `OrchestratedDataSource` ([[orchestration]]) |
| Large datasets needing cross-source JOIN, deterministic | `CrossSourceDataSource` + DuckDB ([[duckdb-cross-source]]) |
| Fixed complex flow that is always the same and mixes HTTP+DB | Custom `DataSource` class ([[custom-datasource]]) |

**Limits the agent (and prompt authors) must respect:**

- **Context window**: a `query()` result returns the full `StructuredResult.rows`
  to the agent. Pass only key fields (IDs, status codes) between hops, not bulk
  rowsets. Use `max_rows` and aggregate SQL (`GROUP BY`) to bound intermediate
  size; a `truncated=true` flag warns when capped.
- **Reliability**: multi-hop reasoning can drop or hallucinate fields. For
  mission-critical fixed flows, encapsulate as a custom `DataSource`
  ([[custom-datasource]]) rather than relying on the agent to re-derive the
  chain every time.
- **Error recovery**: each `query()` returns a structured error on failure
  (`code`/`message`/`suggestion`). The agent decides whether to retry, narrow,
  or surface — deepferry never silently masks a step failure.
- **Cost / latency**: each hop is one LLM turn. Fixed flows repeated often are
  better encapsulated server-side to amortize the cost.

### Operating Modes and Source Provisioning

deepferry runs in two operating modes (see [[project]] § Operating Modes) that
share the query path but differ in **source provisioning capability** and
**credential handling**. The mode is declared at startup:

```toml
[mcp_server]
mode = "desktop"   # or "server" (default)
```

**Tool set varies by mode:**

| Tool | Desktop | Server |
|------|---------|--------|
| `list_sources` / `list_tables` / `schema_info` / `query` | ✅ | ✅ |
| `start_scenario` / `end_scenario` | ✅ | ✅ |
| `propose_source` | ✅ | ❌ (admin-only via Web UI, see [[config-panel]]) |

**`propose_source` — agent-driven provisioning (desktop only).** When the
agent discovers via `list_sources` that a needed source is absent, it may
propose one by submitting its *topology only* — `type`, `host`, `port`,
`database`, `name`. The proposal lands as a `pending` entry; the user confirms
in the desktop UI and enters credentials into the OS keychain (see
[[desktop-app]] § Credential Storage). On confirm, `registry.refresh()` makes
the source queryable. The agent then re-issues `list_sources` to discover it.

**Credentials never cross the LLM context — hard rule, both modes:**

- `propose_source` has **no password/secret field** in its schema.
- `list_sources` / `query` return source topology (id/name/type/health), never credentials.
- `DataSource` resolves credentials from `${ENV_VAR}` (keychain in desktop, Vault/env in server), never from agent-supplied input.
- Audit spans redact `password|secret|token|api_key` to `[REDACTED]` (see [[audit-trace]]).

This rule is why server mode exposes no provisioning tool at all: in a
multi-user production setting, an agent cannot be allowed to wire arbitrary
new data connections, and credentials must never be typed into a prompt.

### Error Handling

All errors returned as structured JSON, NOT Python tracebacks:

```json
{
  "error": {
    "code": "CONNECTION_FAILED",
    "message": "Cannot connect to MySQL at localhost:3306",
    "suggestion": "Check if MySQL is running and credentials are correct"
  }
}
```

Error codes: `CONNECTION_FAILED`, `AUTH_FAILED`, `QUERY_TIMEOUT`, `INVALID_SQL`, `SOURCE_NOT_FOUND`, `WRITE_BLOCKED`, `MISSING_PREDICATE`, `MISSING_LIMIT`

### Configuration

```toml
[mcp_server]
mode = "server"              # "desktop" | "server" — controls tool set + credential source (see § Operating Modes)
transport = "stdio"          # or "http"
http_port = 8080             # only for Streamable HTTP
config_path = "config.toml"  # path to data source config file
```

## Acceptance Criteria (M1 DoD)

1. `pip install deepferry` installs successfully in fresh venv
2. `deepferry mcp-server --config config.toml` starts stdio MCP server
3. Claude Desktop connects and lists ≥3 tools
4. Agent executes `list_tables` → returns table list
5. Agent executes `query` with SQL → returns structured rows
6. Connection failure returns structured error (not traceback)
7. `ruff check .` passes, `mypy --strict` passes
8. Core logic test coverage >70%
9. Agent calls `list_sources` → returns all configured sources with health
10. Agent opens a scenario via `start_scenario("Q3 analysis")` → receives `scenario_id`; subsequent `query` calls with it group under the same scenario in the trace store
11. `end_scenario` closes the scenario; further `query` calls with that id are accepted but mark the scenario reopened (idempotent reopen)
12. `query` with `max_rows` caps output; result carries `truncated=true` when exceeded

## Interview Story

MCP three-primitive (tools/resources/prompts) trade-off: why `tools` are the right abstraction for data access — resources are for static content, prompts are for templating; tools map naturally to parameterized, state-changing queries.

## Dependencies

- [[datasource-abstraction]] — DataSource base class for query execution
- [[sql-datasource]] — MySQL and PostgreSQL implementations
