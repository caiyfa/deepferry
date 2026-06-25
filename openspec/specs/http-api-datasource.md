# Capability: HTTP API Data Source

> **Status**: planned | **Milestone**: M2 | **Owner**: backend

## Summary

Implement an HTTP API data source that treats JSON REST API responses as queryable tables. Supports configuration of URL, HTTP method, headers, and JSON response flattening.

## Motivation

- Many real-world data sources are only accessible via HTTP APIs (SaaS platforms, internal microservices)
- No existing open-source tool supports treating HTTP JSON APIs as first-class data sources for LLM agents
- Combined with two-step auth, this is the killer differentiator

## Specification

### Configuration

```toml
[[sources]]
id = "json-api"
type = "http"
base_url = "https://api.example.com/v1"
default_headers = { Accept = "application/json" }

# Define accessible endpoints as "resources"
[[sources.resources]]
name = "users"
path = "/users"
method = "GET"
description = "List all users"

[[sources.resources]]
name = "orders"
path = "/orders"
method = "GET"
params = { status = "active" }
description = "Active orders"

[[sources.resources]]
name = "create_user"
path = "/users"
method = "POST"
body_template = { name = "{{name}}", email = "{{email}}" }
description = "Create a user (read-only for MVP — visible but blocked)"
```

### JSON Response Flattening

HTTP API responses are often nested JSON. The data source must flatten them into tabular form:

```json
// API response
{
  "data": [
    { "id": 1, "user": { "name": "Alice", "email": "a@b.com" } },
    { "id": 2, "user": { "name": "Bob", "email": "b@c.com" } }
  ]
}
```

Flattened to:

| id | user.name | user.email |
|----|-----------|------------|
| 1 | Alice | a@b.com |
| 2 | Bob | b@c.com |

Flatten strategy:
1. Detect JSON path to array (common keys: `data`, `items`, `results`, `records`, root array)
2. Recursively flatten nested objects: `parent.child` key naming
3. Skip arrays deeper than 1 level (mark as `[complex]`)

### Resource Discovery

`list_resources()` returns configured endpoints (HTTP has no introspection):

```python
async def list_resources(self) -> list[Resource]:
    return [
        Resource(name=r["name"], type="endpoint", description=r.get("description"))
        for r in self._config.resources
    ]
```

### Query Semantics

HTTP "queries" are endpoint invocations:

| Agent Action | Maps To |
|-------------|---------|
| `list_resources()` | List configured endpoints |
| `query(resource="users")` | `GET /users` → flatten response |
| `schema_info("users")` | Sample one response → infer column types |

### Query Parameters

`QueryRequest.params` (a `dict`) is the universal carrier for agent-supplied
values. Its binding depends on the resource's HTTP method, and is the mechanism
that enables **multi-turn agent orchestration** (see [[mcp-server]] § Multi-Turn
Agent Orchestration): an agent passes a value learned in a prior `query()` into
the next one via `params`.

For HTTP sources, `query.statement` carries the **resource name** (the
`[[sources.resources]]` `name` to invoke), and `query.params` is bound as
follows:

| Resource method | `params` binding |
|-----------------|------------------|
| `GET` | URL query string (`?key=value&...`) |
| `POST` / `PUT` / `PATCH` | `body_template` `{{var}}` interpolation, resolved against `params` |

```python
async def execute(self, query: QueryRequest) -> StructuredResult:
    resource = self._find_resource(query.statement)   # statement = resource name
    url = f"{self._config.base_url}{resource.path}"

    if resource.method == "GET":
        if query.params:
            url += "?" + urlencode(query.params)
        response = await self._client.get(url, headers=self._config.default_headers)

    elif resource.method in ("POST", "PUT", "PATCH"):
        # body_template {{var}} tokens resolve from query.params FIRST,
        # then from configured defaults. Unresolved tokens → INVALID_BINDING.
        body = self._render_body_template(resource.body_template, query.params)
        response = await self._client.request(
            resource.method, url, json=body, headers=self._config.default_headers
        )

    return self._flatten_json(response.json(), resource.name)
```

**`body_template` interpolation contract.** Every `{{name}}` token in a
resource's `body_template` MUST resolve against `QueryRequest.params` at query
time (falling back to the resource's configured defaults). This is what lets an
agent POST data it obtained from a previous step — e.g. after querying MySQL,
the agent calls `query("logistics-api", "create_shipment",
params={"order_id": "O-5001", "items": [...]})` and those values flow into the
configured `body_template = { order = "{{order_id}}", items = "{{items}}" }`.

```toml
[[sources.resources]]
name = "create_shipment"
path = "/shipments"
method = "POST"
body_template = { order = "{{order_id}}", items = "{{items}}", warehouse = "WH-01" }
# {{order_id}} and {{items}} resolve from params; "WH-01" is a static default.
```

Unresolved `{{var}}` (present in template, absent from `params` and defaults)
raises `INVALID_BINDING` at execution — never silently sent as a literal
`"{{var}}"` string.

### Production Safeguards

HTTP sources face different risks than SQL: unbounded response sizes, schema
drift, and silent type coercion. The following are mandatory:

| Safeguard | Implementation | Why |
|-----------|----------------|-----|
| **Response size cap** | Reject responses exceeding `max_response_bytes` (default 50MB) with `RESPONSE_TOO_LARGE` | Prevent memory exhaustion from a misbehaving endpoint |
| **Row cap on flatten** | Stop flattening at `max_rows` (default 100,000); set `truncated=true` in result | Unbounded arrays blow up the result set |
| **Explicit schema preferred** | `[[sources.resources]] columns = [...]` declares types; sampling only when omitted (see [[duckdb-cross-source]] § Schema Handling) | Sampling one response is unstable; explicit declaration is production-grade |
| **Pagination** | If endpoint supports `?offset=`/`?cursor=`, fetch up to `max_rows` in pages | Avoid single-request fetches of huge datasets |
| **Request timeout** | Per-request `httpx2` timeout from `QueryRequest.timeout` | Hung endpoints must not block the agent |
| **Status-code mapping** | 4xx → `HTTP_CLIENT_ERROR`; 5xx → `HTTP_SERVER_ERROR`; 401 → reactive auth path (see [[two-step-auth]]) | Errors are structured JSON, never raw tracebacks |

## Acceptance Criteria (M2)

1. Configure a public HTTP API (e.g., JSONPlaceholder) — Agent queries and sees tabular results
2. Nested JSON responses flattened correctly into columns
3. `list_resources` returns configured endpoints
4. Error responses (4xx, 5xx) mapped to structured errors
5. Config validation: missing `base_url` or `resources` raises clear error at startup
6. A response >`max_response_bytes` is rejected with `RESPONSE_TOO_LARGE` (no OOM)
7. An array >`max_rows` is truncated; result carries `truncated=true`
8. An explicitly-declared `columns` schema rejects mismatched response fields (no silent VARCHAR coercion)
9. A paginated endpoint is fetched page-by-page up to `max_rows`, not in one request

## Interview Story

"HTTP APIs are the most common but least supported data source for agents. The flattening algorithm treats JSON structure as an implicit schema — column names become `parent.child` paths. Combined with two-step auth, this enables agents to query any SaaS platform's API as if it were a database table."

## Dependencies

- [[datasource-abstraction]] — Base class
- [[two-step-auth]] — Authentication token injection
