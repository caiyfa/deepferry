# Capability: Orchestration Engine

> **Status**: planned | **Milestone**: M2.5 | **Owner**: backend | **Depends on**: `mvp-init` M2

## Summary

An `OrchestratedDataSource` that executes multi-step request flows —
`login → discover account-permitted instances → route SQL to a chosen instance` —
as a single logical `DataSource`. Steps are declared in `config.toml` with
`{{variable}}` interpolation between steps; the engine supports linear
sequences and `foreach` fan-out over arrays. From the MCP agent's perspective
an orchestrated source is indistinguishable from any other source: one
`query()` call, one `StructuredResult`.

## Motivation

- Enterprise DB query platforms do not expose data through a single request.
  They require authentication, then dynamic discovery of account-permitted
  instances, then per-instance SQL routing. The `mvp-init` HTTP data source is
  atomic (one request → flattened JSON) and cannot express this.
- HTTP resource discovery in `http-api-datasource.md` is **static** —
  `list_resources()` returns only config-declared endpoints. Account-permitted
  instances are only knowable *after* login, so they cannot be enumerated in
  TOML ahead of time.
- Multi-step flows need **variable binding**: the login response's token must
  feed every subsequent step's `Authorization` header; the discover response's
  instance list must feed the query step's URL. No such mechanism exists today.
- Keeping orchestration inside the `DataSource` ABC (not above MCP) preserves
  tool uniformity: the agent stays simple, the registry stays uniform, MCP stays
  stateless. See `orchestration-audit/design.md` decision #1.

## Specification

### DataSource Subtype

`OrchestratedDataSource` implements the same five `DataSource` ABC methods. The
MCP server, registry, and config panel treat it identically to MySQL / HTTP
sources — they never branch on `isinstance`.

```python
class OrchestratedDataSource(DataSource):
    """A data source backed by a multi-step request flow."""

    def __init__(self, config: OrchestrationConfig, http_client, token_manager, trace_sink):
        self._config = config
        self._engine = OrchestrationEngine(http_client, token_manager, trace_sink)
        # Dynamic discovery cache (populated lazily by discover())
        self._discovered: list[Resource] | None = None

    async def execute(self, query: QueryRequest) -> StructuredResult:
        # query.statement is the SQL the agent wants; it is bound into the
        # designated "query" step via {{sql}} (or a configured input key).
        return await self._engine.run(self._config, inputs={"sql": query.statement,
                                                             "params": query.params})

    async def list_resources(self) -> list[Resource]:
        # Dynamic discovery: run the flow up to and including the discover step,
        # then return the discovered instances as Resources. Cached per-source
        # with a TTL (see Dynamic Discovery Contract).
        if self._discovered is None or self._discovery_expired():
            self._discovered = await self._engine.discover(self._config)
        return self._discovered

    async def schema_info(self, resource: str | None = None) -> Schema:
        # Invoke the flow's "schema" step (if declared) for the selected instance,
        # else sample one query and infer.
        ...

    async def health_check(self) -> HealthStatus:
        # Run only the auth/login step; report latency + ok.
        ...
```

### Dynamic Discovery Contract

Static HTTP sources declare resources in TOML. Orchestrated sources **discover**
them at runtime. The contract:

| Aspect | Rule |
|--------|------|
| When | `list_resources()` is called for the first time, or the discovery cache TTL (default 300s) has expired |
| How | The engine runs steps up to and including the step marked `kind = "discover"`, then materializes its output array into `list[Resource]` |
| Cache | In-memory + invalidation on `auth_retry` (permissions may have changed) |
| Isolation | Discovery never executes the "query" step — it must not run user SQL merely to list instances |

### Step Model

An orchestration flow is an ordered list of steps. Each step is one HTTP call
plus input/output bindings.

```python
class Step(BaseModel):
    name: str                         # unique within flow, used in {{name.field}}
    kind: StepKind = "request"        # "auth" | "discover" | "request"
    method: str = "GET"
    path: str                         # may contain {{...}} interpolations
    headers: dict[str, str] = {}      # values may contain {{...}}
    body_template: dict | None = None # values may contain {{...}}
    output: dict[str, str] = {}       # JSON-path-ish extractors: alias -> path
    foreach: ForeachConfig | None = None
    retry: AuthRetryConfig | None = None   # per-step override of 401 behavior

class StepKind(str, Enum):
    auth = "auth"            # produces a token consumed by later steps
    discover = "discover"    # produces the dynamic resource list
    request = "request"      # ordinary data fetch / SQL route

class ForeachConfig(BaseModel):
    over: str                # {{upstream.array}} to iterate
    as: str = "item"         # loop variable name
    mode: ForeachMode = "merge"  # "merge" (UNION) | "separate"

class OrchestrationConfig(BaseModel):
    id: str
    base_url: str
    auth: AuthConfig | None = None    # reused from two-step-auth
    steps: list[Step]
    discovery_ttl_seconds: int = 300
```

**Supported topologies** (hard cap — see design.md decision #3):

| Topology | Example | Supported |
|----------|---------|-----------|
| Linear | login → discover → query | ✅ |
| Foreach | discover → query-each-instance → merge | ✅ |
| Conditional (`if`) | branch on response field | ❌ use a dedicated DataSource impl |
| Cycle (`while`) | poll until ready | ❌ out of scope |
| Arbitrary DAG | multi-branch parallel | ❌ use Airflow |

### Variable Interpolation

A Jinja-subset evaluator resolves `{{ ... }}` tokens in `path`, `headers`, and
`body_template` against the accumulated step outputs + the agent's input.

```
Available scopes:
  {{sql}}                  — the agent's query.statement
  {{params.x}}             — the agent's query.params["x"]
  {{steps.login.token}}    — the "token" alias extracted from step "login"
  {{steps.discover.instances[0].id}} — indexed array access
  {{item.id}}              — the loop variable inside a foreach step

Rejected (INVALID_BINDING at config validation, never at runtime):
  {{ steps.login.token | upper }}   — filters forbidden
  {% if ... %}                       — control flow forbidden
  {{ unknown_step.x }}              — forward / undefined refs
```

Extraction uses dot-notation JSON paths against each step's parsed JSON
response. The `output` map names those extractions so downstream steps refer to
stable aliases rather than raw paths:

```toml
[[sources.steps]]
name = "login"
kind = "auth"
method = "POST"
path = "/auth/login"
body_template = { username = "${API_USER}", password = "${API_PASS}" }
output = { token = "access_token", expires_in = "expires_in" }
# After this step: {{steps.login.token}} is bound to response.access_token
```

### Foreach Fan-out

When a step declares `foreach`, the engine iterates the bound array and executes
the step's request once per element, binding `{{item}}` (or the configured loop
name) per iteration.

```toml
[[sources.steps]]
name = "query_each"
foreach = { over = "{{steps.discover.instances}}", as = "inst", mode = "merge" }
method = "POST"
path = "/instances/{{inst.id}}/query"
headers = { Authorization = "Bearer {{steps.login.token}}" }
body_template = { sql = "{{sql}}" }
output = { rows = "data" }
```

| `mode` | Result shape | When to use |
|--------|--------------|-------------|
| `merge` (default) | All per-iteration row sets UNIONed into one `StructuredResult` | Same schema across instances (the common case) |
| `separate` | Each iteration's result kept as a distinct dataset | Heterogeneous schemas; agent/inspector compares them side by side |

`merge` requires every iteration to produce the same column set; a mismatch
raises `FOREACH_SCHEMA_MISMATCH` with the divergent column lists.

### Error Handling

| Failure | Code | Behavior |
|---------|------|----------|
| Interpolation references unknown step/field | `INVALID_BINDING` | Raised at config load (startup), never at runtime |
| Step HTTP status ≥ 500 | `STEP_FAILED` | Engine stops; spans record the failing step + accumulated bindings |
| Step HTTP 401 (and `retry` enabled) | (handled inline) | Reactive auth path from `two-step-auth.md` runs **within** the step — see integration below |
| Foreach iteration fails mid-array | `STEP_FAILED` | Partial results from prior iterations are returned in the span summary; engine does not silently drop them |
| Auth step itself fails | `AUTH_FAILED` | Terminal; no downstream steps run |

Every failure is recorded as a span with `status = "error"` and a redacted
summary — see `audit-trace.md`. The MCP agent receives a structured error
object, never a Python traceback (`AGENTS.md` First Principle #5).

### Integration with Two-Step Auth

Steps with `kind = "auth"` (or any step carrying `Authorization` headers backed
by a cached token) reuse the existing `TokenManager`. The reactive 401 path
(`two-step-auth.md` § Reactive Invalidation) fires **inside** the step's request
runner — the engine itself is auth-agnostic. This means:

- Token caching, TTL pre-emption, concurrent deduplication — all inherited.
- 401 → invalidate → re-login → single retry — inherited, opens an `auth_retry`
  child span under the step's span.
- An orchestrated flow with one `auth` step triggers at most one login across N
  concurrent queries (the `two-step-auth.md` acceptance criterion #6 invariant).

### Integration with Trace

The engine is the primary span producer. Per `audit-trace.md`:

- One **root span** per `execute()` call (`kind = "orchestration"`).
- One **child span** per step (`kind` = the step's `kind`).
- foreach iterations open **grandchild spans** sharing the step's parent.
- auth_retry opens a grandchild `auth_retry` span under the step span.

The agent sees only the final `StructuredResult` (or structured error); the full
span tree is exposed to humans via `/api/executions/:id` and the desktop detail
view.

### Configuration

```toml
[[sources]]
id = "db-platform"
type = "orchestrated"
base_url = "https://query.platform.internal/v1"

[sources.auth]                      # reused from two-step-auth
login_path = "/auth/login"
login_method = "POST"
login_body = { username = "${PLATFORM_USER}", password = "${PLATFORM_PASS}" }
token_field = "access_token"
token_type = "bearer"
token_ttl = 3600

discovery_ttl_seconds = 300         # cache discovered instances for 5 min

[[sources.steps]]
name = "login"
kind = "auth"
method = "POST"
path = "/auth/login"
body_template = { username = "${PLATFORM_USER}", password = "${PLATFORM_PASS}" }
output = { token = "access_token" }

[[sources.steps]]
name = "discover"
kind = "discover"
method = "GET"
path = "/instances"
headers = { Authorization = "Bearer {{steps.login.token}}" }
output = { instances = "data.instances" }   # bind {{steps.discover.instances}}

[[sources.steps]]
name = "query"
kind = "request"
foreach = { over = "{{steps.discover.instances}}", as = "inst", mode = "merge" }
method = "POST"
path = "/instances/{{inst.id}}/query"
headers = { Authorization = "Bearer {{steps.login.token}}" }
body_template = { sql = "{{sql}}", params = "{{params}}" }
output = { rows = "data" }
```

## Acceptance Criteria (M2.5)

1. Configure a mock DB-query-platform (login + discover + query endpoints) —
   agent's single `query()` call returns merged rows from all discovered
   instances.
2. `list_resources()` on an orchestrated source returns the **dynamic** instance
   list from the discover step (not a static TOML list), and the result is
   cached for `discovery_ttl_seconds`.
3. A `foreach` over 3 discovered instances returns a merged `StructuredResult`
   with 3× the per-instance row count (same schema).
4. A `401` from the query step triggers one re-login + one retry; on success the
   merged result is returned and the span tree records `auth_retry` spans.
5. An undefined `{{steps.foo.bar}}` binding is rejected at startup
   (`INVALID_BINDING`), not at query time.
6. A mid-foreach failure on instance #2 of 3 returns `STEP_FAILED` with the
   partial results of instance #1 visible in the span summary.
7. 5 concurrent `query()` calls against the same orchestrated source trigger
   exactly one login (concurrency invariant preserved).
8. The full span tree (root + per-step + foreach grandchildren) is queryable via
   `/api/executions/:id`.
9. `ruff check .` and `mypy --strict` pass; core engine coverage >70%.

## Interview Story

"The hard part wasn't building a workflow engine — it was refusing to. Real
production data platforms need login→discover→route, which looks like it begs
for a DAG orchestrator. I deliberately capped deepferry at linear + foreach,
because branching logic in declarative TOML becomes unmaintainable, and a query
ferry is not Airflow. The orchestration engine is a DataSource subtype, so MCP
stays stateless and the agent stays dumb — one `query()` call, one result. The
401 reactive-auth path reuses the existing TokenManager Lock, so the concurrency
invariant from two-step auth holds even inside a multi-step flow."

## Dependencies

- [[datasource-abstraction]] — Implements the ABC; adds the subtype + dynamic discovery contract
- [[two-step-auth]] — Reuses TokenManager + reactive 401 path
- [[http-api-datasource]] — Step requests use the same httpx2 + JSON flattening
- [[audit-trace]] — Engine emits the span tree; without it, flows are a black box
