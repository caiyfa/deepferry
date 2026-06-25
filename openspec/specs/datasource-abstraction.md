# Capability: DataSource Abstraction Layer

> **Status**: planned | **Milestone**: M1 | **Owner**: backend

## Summary

Define a unified `DataSource` abstract base class that all data source implementations (MySQL, PostgreSQL, HTTP API) must implement. The goal: adding a new SQL data source takes <0.5 days.

## Motivation

- Heterogeneous data sources (SQL databases, HTTP APIs) have fundamentally different access patterns
- Without abstraction, each data source duplicates connection management, error handling, result formatting
- Good abstraction is the competitive moat — proves engineering maturity

## Specification

### Abstract Interface

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel

class DataSource(ABC):
    """Unified interface for all data sources."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def execute(self, query: QueryRequest) -> StructuredResult: ...

    @abstractmethod
    async def list_resources(self) -> list[Resource]: ...

    @abstractmethod
    async def schema_info(self, resource: str | None = None) -> Schema: ...

    @abstractmethod
    async def health_check(self) -> HealthStatus: ...
```

### Type Definitions (Pydantic v2)

```python
class QueryRequest(BaseModel):
    source_id: str
    statement: str
    params: dict | None = None
    timeout: int = 30

class StructuredResult(BaseModel):
    columns: list[ColumnMeta]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float

class ColumnMeta(BaseModel):
    name: str
    type: str
    nullable: bool = True

class Resource(BaseModel):
    name: str
    type: str              # "table", "view", "endpoint"
    description: str | None = None

class Schema(BaseModel):
    resources: list[ResourceMeta]

class ResourceMeta(BaseModel):
    name: str
    columns: list[ColumnMeta]

class HealthStatus(BaseModel):
    ok: bool
    latency_ms: float
    error: str | None = None
```

### Extensibility Contract

1. **To add a new SQL data source**: inherit `DataSource`, implement 5 methods, register in `config.toml` — target <0.5 days
2. **To add a new non-SQL data source** (e.g., GraphQL): same interface, different execution semantics — target <1 day
3. Source discovery: auto-detect via `config.toml` `[[sources]]` blocks, instantiate by `type` field
4. **To add an orchestrated multi-step source** (login → discover → route query): inherit `OrchestratedDataSource`, declare ordered steps in `config.toml` — see [[orchestration]]. Resources are discovered **dynamically** at runtime, not declared statically.
5. **To add a custom imperative source** (fixed complex flow that **mixes HTTP and DB**, or needs conditional/loop logic the declarative engine rejects): inherit `DataSource` and implement `execute()` directly in Python — see [[custom-datasource]]. This is the code-level escape hatch: full language expressiveness (httpx + asyncmy in one `execute()`), at the cost of writing/maintaining a module. Resources are declared statically in code or discovered by the source's own logic.

### Dynamic vs Static Discovery

Not every source knows its resources at config-load time. The discovery model
differs by source type:

| Source type | `list_resources()` returns | When known |
|-------------|---------------------------|------------|
| SQL (MySQL/PG) | Live `SHOW TABLES` / catalog query | At `connect()` time |
| HTTP (static) | Config-declared `[[sources.resources]]` | At config load |
| Orchestrated | Output of the flow's `discover` step | At first call (post-auth), cached per `discovery_ttl_seconds` |
| Custom | Code-declared (or source-defined discovery) | At `connect()` / per the source's own logic |

SQL and static-HTTP sources are **introspectable** (they have a schema or a
config to read). Orchestrated sources are **discovered** — the resource list
itself is the product of a request flow and may change with account
permissions, so it must be fetched and cached, not declared. See
[[orchestration]] § Dynamic Discovery Contract.

### Configuration Format

```toml
[[sources]]
id = "my-mysql"
type = "mysql"
host = "localhost"
port = 3306
database = "mydb"
user = "${MYSQL_USER}"     # env var injection
password = "${MYSQL_PASS}"

[[sources]]
id = "my-pg"
type = "postgresql"
host = "localhost"
port = 5432
database = "mydb"
user = "${PG_USER}"
password = "${PG_PASS}"

[[sources]]
id = "json-api"
type = "http"
base_url = "https://api.example.com"
method = "GET"
headers = { Authorization = "Bearer ${API_TOKEN}" }

[[sources]]
id = "db-platform"
type = "orchestrated"          # multi-step flow; see [[orchestration]]
base_url = "https://query.platform.internal/v1"
discovery_ttl_seconds = 300    # dynamic resource list cache

[[sources.steps]]
name = "login"
kind = "auth"
# ... (full step declaration in [[orchestration]])
```

### Registry Lifecycle (Hot Reload)

Sources are loaded into an in-memory `SourceRegistry` at process startup, but
the registry is **refreshable at runtime** — `config-panel` CRUD writes
([[config-panel]] § Source Governance) and confirmed `propose_source` calls
([[mcp-server]] § Operating Modes) take effect on the next `query()` without a
process restart. The refresh contract:

**Trigger.** Refresh is **explicit**, invoked by the config-panel side after a
successful atomic config write (write-to-temp + `os.rename`). There is no file
watcher — config-panel CRUD is the single mutation entry point, so passive
watching adds risk (reorder, partial write) without benefit. `uvicorn --reload`
/ `SIGHUP` process reload are explicitly **not** used: those reload the whole
process and break every in-flight connection, violating the "next query"
contract.

**Concurrency model: Copy-on-Write.** `refresh()` builds a new instances dict,
then atomically swaps the registry's internal pointer (a single attribute
assignment under the GIL). `query()` always reads the current pointer.

```
refresh():
    new = { src.id: instantiate(src) for src in load(config_path) }   # lazy connect
    added, removed, edited = diff(self._instances, new)
    self._instances = new                 # atomic pointer swap
    schedule_drain(removed | edited)      # background task, non-blocking
```

In-flight queries hold a reference to the source instance they started with;
Python GC keeps that instance alive until the query returns. New queries see
the fresh registry immediately.

**Per-operation semantics:**

| Operation | New registry | Old instance |
|-----------|--------------|--------------|
| ADD | New instance (connection pool built lazily on first `execute()`) | — |
| EDIT | New instance with new config + freshly-resolved `${ENV_VAR}` | draining |
| DELETE | Removed (subsequent lookups raise `SOURCE_NOT_FOUND`) | draining |

**Graceful drain.** Removed/edited instances enter a `draining` state and are
closed in a background `asyncio` task after a graceful period (default 30s,
configurable, ≥ statement timeout). If all in-flight references drop earlier,
the instance is closed immediately. After the graceful period the pool is
force-disposed; any leaked connection is caught by the database's own idle
timeout as a last line of defense. Drain never blocks `refresh()` from
returning.

**DuckDB ATTACH serialization.** When a source participating in cross-source
federation ([[duckdb-cross-source]]) is added or removed, the corresponding
`ATTACH`/`DETACH` runs under the DuckDB writer lock (ATTACH is serialized,
line 199 of [[duckdb-cross-source]]). Cross-source queries already in flight
hold their own DuckDB connection with its snapshot of attached sources.

**Audit.** Every `refresh()` records what changed (added/edited/removed source
ids, who triggered it) in the trace store, with credential fields redacted
per [[audit-trace]].

## Acceptance Criteria

1. `DataSource` base class defined with 5 abstract methods
2. MySQL and PostgreSQL both implement the interface
3. HTTP API implements the interface (M2)
4. Adding a 3rd SQL source (e.g., SQLite) takes <2 hours from scratch
5. All implementations share error handling, result formatting, connection management via base class
6. `OrchestratedDataSource` implements the ABC; `list_resources()` returns dynamically discovered resources (post-auth), cached per `discovery_ttl_seconds` — not a static TOML list

## Interview Story

"Unifying schema-ed (SQL) and schema-less (HTTP) data sources under one interface. The challenge: SQL has introspection (SHOW TABLES), HTTP doesn't — so `list_resources()` for HTTP returns configured endpoints. The key insight: the interface asks *what* a source exposes, not *how* it's stored internally."

## Dependencies

- [[mcp-server]] — MCP tools call DataSource methods
- [[sql-datasource]] — First two implementations
- [[http-api-datasource]] — Third implementation (M2)
- [[orchestration]] — `OrchestratedDataSource` subtype with dynamic discovery (M2.5)
