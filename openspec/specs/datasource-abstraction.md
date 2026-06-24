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
```

## Acceptance Criteria

1. `DataSource` base class defined with 5 abstract methods
2. MySQL and PostgreSQL both implement the interface
3. HTTP API implements the interface (M2)
4. Adding a 3rd SQL source (e.g., SQLite) takes <2 hours from scratch
5. All implementations share error handling, result formatting, connection management via base class

## Interview Story

"Unifying schema-ed (SQL) and schema-less (HTTP) data sources under one interface. The challenge: SQL has introspection (SHOW TABLES), HTTP doesn't — so `list_resources()` for HTTP returns configured endpoints. The key insight: the interface asks *what* a source exposes, not *how* it's stored internally."

## Dependencies

- [[mcp-server]] — MCP tools call DataSource methods
- [[sql-datasource]] — First two implementations
- [[http-api-datasource]] — Third implementation (M2)
