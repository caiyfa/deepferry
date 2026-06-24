# Technical Design: MVP Initialization

> **Change ID**: `mvp-init` | **Status**: proposed

## Architecture Decisions

### 1. Language: Python (not Rust)

**Decision**: Pure Python for MVP.

**Rationale**:
- IO-bound workload: 95% of time spent in network IO or C extensions (asyncmy, DuckDB, Pydantic/Rust, orjson)
- Python glue code is <5% of execution time
- Rewriting in Rust would take 6-8 extra months and miss the job search window
- If profiling reveals hot spots, use PyO3 for local acceleration (not full rewrite)

**Rejected Alternative**: Rust reimplementation.
- Second-System Trap: rewrite never finishes
- Not a translation — a redesign (asyncio→tokio, GC→ownership)
- DeepSeek itself uses Python orchestration + C++/CUDA compute — same pattern

### 2. Desktop: Tauri (not Electron)

**Decision**: Tauri 2.x with React frontend.

**Rationale**:
- Bundle size: 50-80MB (Tauri) vs 150-250MB (Electron)
- Rust backend for sidecar management eliminates Node.js dependency
- `@tauri-apps/plugin-http` bypasses CORS for local FastAPI access
- Sidecar lifecycle management built into Tauri

**Rejected Alternative**: Electron
- 2026: no reason to choose Electron for greenfield projects
- Larger attack surface (Chromium + Node.js)

### 3. Data Grid: AG Grid Community (not Univer)

**Decision**: AG Grid Community (MIT).

**Rationale**:
- deepferry displays query results → Data Grid, not a Spreadsheet
- AG Grid: virtual scrolling, sorting, filtering, inline editing — all in MIT license
- Univer would add unnecessary complexity (formula engine, multi-sheet document model)

**When to Revisit**: If users demand Excel formulas or multi-sheet, migrate to Univer OSS (Apache 2.0).

### 4. Storage: Three-Tier Separation

**Decision**: config.toml + SQLite + DuckDB (never mixed).

| Tier | Storage | Rationale |
|------|---------|-----------|
| Config | `config.toml` (YAML/TOML) | Declarative, human-editable, git-diffable |
| Runtime | SQLite (`app.db`) | Append-only (query history, token cache, schema cache) |
| Analytics | DuckDB | Columnar OLAP (M4 cross-source JOIN only) |
| Frontend | SQLite (`ui.db`) | Tauri UI state (recent queries, column widths, preferences) |

**Anti-patterns Avoided**:
- Storing config in SQLite (violates declarative principle, can't vim/git diff)
- Caching query results in SQLite (overlaps DuckDB's responsibility)
- Storing MCP session state (MCP is stateless by design)

### 5. Async: Full Async Stack

**Decision**: Every IO operation is async — no sync blocking anywhere.

**Implementation**:
- MySQL: `asyncmy` (Cython-accelerated, 22-28% faster than aiomysql)
- PostgreSQL: `asyncpg` (native async, industry standard)
- HTTP: `httpx2` (Pydantic team fork of httpx, HTTP/2 support)
- Web: FastAPI async routes + uvicorn
- SQLite: `aiosqlite` + standard library `sqlite3`

### 6. Config: TOML (not YAML, not JSON)

**Decision**: TOML for `config.toml`.

**Rationale**:
- MCP ecosystem convention (dbhub uses TOML)
- Python ecosystem standard (pyproject.toml, uv)
- Better readability than JSON, less ambiguous than YAML

### 7. Package Manager: uv (not poetry, not pip)

**Decision**: `uv` for dependency management and virtual environments.

**Rationale**:
- Rust-implemented, 10-100x faster than pip
- Native pyproject.toml support (PEP 621)
- Built-in virtual environment management
- Same tool for installing, locking, and running

### 8. Linting: ruff (not flake8+isort+black)

**Decision**: Single tool: `ruff`.

**Rationale**:
- Rust-implemented, near-instant
- Replaces flake8 (linting) + isort (import sorting) + black (formatting)
- Over 800 built-in rules

## Component Interaction Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         Entry Points                              │
│                                                                    │
│  Claude Desktop ──stdio──▶ MCP Server                             │
│  Any MCP Client ──HTTP──▶ MCP Server (Streamable HTTP)           │
│  Tauri Desktop  ──HTTP──▶ FastAPI Sidecar                        │
│                                                                    │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      FastAPI Core        │
                    │                          │
                    │  ┌────────────────────┐  │
                    │  │   Source Registry   │  │
                    │  │  (from config.toml) │  │
                    │  └────────┬───────────┘  │
                    │           │              │
                    │  ┌────────▼───────────┐  │
                    │  │  DataSourceFactory  │  │
                    │  │  type → class map   │  │
                    │  └────────┬───────────┘  │
                    │           │              │
                    └───────────┼──────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
  ┌───────▼───────┐   ┌────────▼────────┐   ┌───────▼───────┐
  │ MySQL Source   │   │ PostgreSQL Src   │   │ HTTP Source    │
  │ · asyncmy pool │   │ · asyncpg pool   │   │ · httpx2       │
  │ · SQL dialect  │   │ · SQL dialect    │   │ · flatten JSON │
  └───────┬───────┘   └────────┬────────┘   └───────┬───────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   DataSource ABC     │
                    │   · execute()        │
                    │   · list_resources() │
                    │   · schema_info()    │
                    │   · health_check()   │
                    └─────────────────────┘
```

## Data Flow: Agent Query

```
1. Agent sends: query(source_id="my-db", sql="SELECT * FROM users")
                    │
2. MCP Server receives tool call
                    │
3. FastAPI resolves source_id → DataSource instance
                    │
4. DataSource.execute(query)
   ├── MySQL:     asyncmy pool → cursor.execute → fetchall
   ├── PostgreSQL: asyncpg pool → fetch → dict rows
   └── HTTP:      httpx2 → GET /users → flatten JSON
                    │
5. StructuredResult returned
   {
     "columns": [{"name": "id", "type": "int"}, ...],
     "rows": [{"id": 1, "name": "Alice"}, ...],
     "row_count": 10,
     "execution_time_ms": 23.5
   }
                    │
6. Logged to query_history (SQLite)
                    │
7. Returned to Agent via MCP
```

## Data Flow: Two-Step Auth

```
First query (no cached token):
  Agent query → HTTP Source → check cache (miss)
  → POST /auth/login → extract token_field
  → INSERT INTO token_cache → inject token → GET /data
  → return result

Second query (valid cached token):
  Agent query → HTTP Source → check cache (hit, not expired)
  → inject token → GET /data
  → return result

Concurrent (5 simultaneous queries):
  Query 1 → check cache (miss) → acquire lock → POST /auth/login
  Queries 2-5 → check cache (miss) → wait for lock
  Query 1 → login success → cache token → release lock
  Queries 2-5 → acquire lock → check cache (hit!) → inject → query
  → All 5 queries succeed, login called exactly ONCE
```
