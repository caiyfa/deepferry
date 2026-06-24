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

```python
async def execute(self, query: QueryRequest) -> StructuredResult:
    resource = self._find_resource(query.resource_name)
    # Build URL with query params
    url = f"{self._config.base_url}{resource.path}"
    if query.params:
        url += "?" + urlencode(query.params)
    # Execute request
    response = await self._client.get(url, headers=self._config.default_headers)
    # Flatten and return
    return self._flatten_json(response.json(), resource.name)
```

## Acceptance Criteria (M2)

1. Configure a public HTTP API (e.g., JSONPlaceholder) — Agent queries and sees tabular results
2. Nested JSON responses flattened correctly into columns
3. `list_resources` returns configured endpoints
4. Error responses (4xx, 5xx) mapped to structured errors
5. Config validation: missing `base_url` or `resources` raises clear error at startup

## Interview Story

"HTTP APIs are the most common but least supported data source for agents. The flattening algorithm treats JSON structure as an implicit schema — column names become `parent.child` paths. Combined with two-step auth, this enables agents to query any SaaS platform's API as if it were a database table."

## Dependencies

- [[datasource-abstraction]] — Base class
- [[two-step-auth]] — Authentication token injection
