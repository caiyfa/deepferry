# Implementation Tasks: MVP Initialization

> **Change ID**: `mvp-init` | **Total**: 16.5 weeks | **4 milestones**

---

## Milestone 1: MCP Core + Relational Databases (Week 1-5)

### Week 1: Project Setup
- [ ] Initialize Python project with `uv init`
- [ ] Create `pyproject.toml` with locked dependency versions
- [ ] Set up `ruff` config (pyproject.toml `[tool.ruff]`)
- [ ] Set up `mypy` config (strict mode)
- [ ] Create directory structure (src/deepferry/, tests/, frontend/)
- [ ] Write `config.example.toml`
- [ ] Set up `.gitignore` (Python + Tauri + IDE)
- [ ] Set up GitHub Actions skeleton (lint on push)

### Week 2: DataSource Abstraction + MySQL
- [ ] Define `DataSource` ABC with 5 abstract methods
- [ ] Define Pydantic models: `QueryRequest`, `StructuredResult`, `Schema`, `Resource`, `HealthStatus`
- [ ] Implement `SourceRegistry` (config.toml parsing → DataSource instances)
- [ ] Implement `MySQLDataSource` with asyncmy:
  - [ ] Connection pool management
  - [ ] `execute()` — parameterized queries, row→dict conversion
  - [ ] `list_resources()` — `SHOW TABLES` or information_schema
  - [ ] `schema_info()` — `DESCRIBE` or information_schema
  - [ ] `health_check()` — `SELECT 1` with timeout
  - [ ] Error mapping (OperationalError → CONNECTION_FAILED, etc.)
- [ ] Write unit tests for MySQL source (mock connection)
- [ ] Write integration tests (Docker mysql:8)

### Week 3: PostgreSQL Data Source
- [ ] Lock `asyncpg` version in pyproject.toml
- [ ] Implement `PostgreSQLDataSource`:
  - [ ] Connection pool management (asyncpg.Pool)
  - [ ] `execute()` — parameterized queries ($1, $2 style)
  - [ ] `list_resources()` — pg_catalog.pg_tables
  - [ ] `schema_info()` — information_schema.columns
  - [ ] `health_check()` — `SELECT 1`
- [ ] Write integration tests (Docker postgres:16)
- [ ] Verify: adding PostgreSQL took <0.5 days after MySQL was done
- [ ] Add `postgres:16` to CI Docker services

### Week 4: MCP Server
- [ ] Set up MCP Server with `mcp` Python SDK
  - [ ] stdio transport (for Claude Desktop)
  - [ ] Streamable HTTP transport (for remote access)
- [ ] Register tools:
  - [ ] `list_tables(source_id: str) → [TableInfo]`
  - [ ] `query(source_id: str, sql: str, params?: dict) → QueryResult`
  - [ ] `schema_info(source_id: str, table?: str) → SchemaInfo`
- [ ] Structured error handling (no tracebacks exposed to agent)
- [ ] CLI: `deepferry mcp-server --config config.toml`
- [ ] Write `claude_desktop_config.json` example
- [ ] Test with Claude Desktop: connect → list tools → query MySQL → query PostgreSQL

### Week 5: Web Config Panel (Basic) + M1 Polish
- [ ] FastAPI app with basic routes
- [ ] Source CRUD API (→ config.toml writes)
- [ ] Connection test endpoint
- [ ] Minimal HTML config panel (vanilla JS, no framework)
- [ ] M1 acceptance script: full walkthrough from pip install to agent query
- [ ] README.md draft (English)
- [ ] M1 go/no-go review

---

## Milestone 2: HTTP API + Two-Step Auth (Week 6-9)

### Week 6: HTTP API Data Source
- [ ] Implement `HttpApiDataSource`:
  - [ ] httpx2 async client with connection pooling
  - [ ] `list_resources()` — return configured endpoints
  - [ ] `execute()` — GET/POST endpoint → JSON response
  - [ ] JSON flattening algorithm (nested objects → parent.child columns)
  - [ ] Array detection: find common keys (`data`, `items`, `results`, `records`)
  - [ ] `schema_info()` — sample one response, infer column types
  - [ ] `health_check()` — HEAD request to base_url
- [ ] Config validation: `base_url` required, `resources` list required
- [ ] Test with JSONPlaceholder (public API, no auth)

### Week 7: JSON Flattening Polish
- [ ] Handle edge cases:
  - [ ] Deeply nested objects (>2 levels) → truncate to 2 levels
  - [ ] Mixed types across rows → coalesce to VARCHAR
  - [ ] Null handling → preserve as NULL
  - [ ] Large arrays → sample first N elements
- [ ] Write comprehensive flattening tests (50+ test cases)
- [ ] Performance test: 10K row JSON response <1s processing

### Week 8: Two-Step Authentication
- [ ] Implement `TokenManager`:
  - [ ] SQLite-backed token cache (survives restart)
  - [ ] Login flow: POST credentials → extract token → cache
  - [ ] Token injection: `bearer`, `header:X-Custom`, `query:token`
  - [ ] TTL-based expiration detection
  - [ ] Auto-refresh before expiration (configurable buffer)
  - [ ] Concurrent safety: `asyncio.Lock` per source_id
- [ ] Write mock two-step auth server (FastAPI test fixture):
  - [ ] `/auth/login` → returns `{access_token, expires_in}`
  - [ ] `/data` → requires `Authorization: Bearer <token>`
  - [ ] Token validation + expiration simulation

### Week 9: Auth Testing + M2 Polish
- [ ] Test: first query triggers login (verify: login called once)
- [ ] Test: second query reuses cache (verify: login NOT called)
- [ ] Test: expired token triggers silent refresh
- [ ] Test: 5 concurrent queries → login called exactly once
- [ ] Test: token survives process restart
- [ ] M2 acceptance script
- [ ] M2 go/no-go review

---

## Milestone 3: Desktop Table Application (Week 10-14)

### Week 10: Tauri Scaffold
- [ ] Initialize Tauri 2.x project (`npm create tauri-app`)
- [ ] Set up React + TypeScript + Vite
- [ ] Configure sidecar: Python process bundled via PyInstaller
- [ ] Implement sidecar lifecycle:
  - [ ] Start FastAPI on app launch
  - [ ] Health check loop
  - [ ] Kill on app close
- [ ] Dev mode: `uvicorn --reload` + `npm run tauri dev`
- [ ] Ship minimal "hello world" sidecar → verify Win + Mac

### Week 11: Frontend Core
- [ ] `DataSourceSelector` component:
  - [ ] Fetch data sources from FastAPI
  - [ ] Display source name, type, health status (green/red)
  - [ ] Select source → show tables/resources
- [ ] `QueryEditor` component:
  - [ ] SQL textarea for SQL sources
  - [ ] Endpoint selector for HTTP sources
  - [ ] Execute button → POST /api/query
- [ ] API client layer (TypeScript, typed requests/responses)

### Week 12: AG Grid Integration
- [ ] `ResultGrid` component:
  - [ ] AG Grid Community setup with virtual scrolling
  - [ ] Column definitions from `StructuredResult.columns`
  - [ ] Sort (click header)
  - [ ] Filter (per-column text/number)
  - [ ] Inline editing (double-click cell, frontend only)
  - [ ] Column resize
- [ ] CSV export button
- [ ] Performance test: 100K rows <500ms render, <100ms scroll

### Week 13: History + State
- [ ] `QueryHistory` component:
  - [ ] Fetch history from `/api/history` (paginated)
  - [ ] Filter by source, status
  - [ ] Click to re-run past query
- [ ] Tauri-side SQLite (`ui.db`):
  - [ ] Recent data sources
  - [ ] Recent queries
  - [ ] Column widths, sort preferences
  - [ ] Favorites
- [ ] Keyboard shortcuts (Cmd/Ctrl+Enter to execute)

### Week 14: Packaging + M3 Polish
- [ ] PyInstaller packaging for FastAPI sidecar
- [ ] Tauri build for macOS (.dmg)
- [ ] Tauri build for Windows (.msi)
- [ ] End-to-end test: install → launch → query → view → export
- [ ] M3 acceptance script
- [ ] M3 go/no-go review

---

## Milestone 4: DuckDB Cross-Source JOIN + Open Source Polish (Week 15-18)

### Week 15: DuckDB Foundation
- [ ] Set up DuckDB with mysql_scanner and postgres_scanner extensions
- [ ] Implement `ATTACH` for MySQL and PostgreSQL sources
- [ ] Test: DuckDB can query MySQL table directly
- [ ] Test: DuckDB can query PostgreSQL table directly

### Week 16: JSON Materialization
- [ ] Implement `SchemaInferrer`:
  - [ ] Sample N rows from HTTP API response
  - [ ] Detect column types across samples
  - [ ] Coalesce: int > float > varchar (widest wins)
  - [ ] Handle NULLs, nested objects, arrays
- [ ] Implement materialization:
  - [ ] Fetch HTTP API data → infer schema → `CREATE TABLE AS`
  - [ ] `REFRESH` mechanism for re-fetch
- [ ] Test: MySQL customers JOIN HTTP orders → correct results

### Week 17: Demo + Degradation Path
- [ ] Build demo scenario (e-commerce: customers in MySQL, orders in HTTP API)
- [ ] Verify full pipeline: agent query → two-step auth → fetch → materialize → JOIN → display
- [ ] Implement degradation path: DuckDB fails → Python pandas join with warning
- [ ] Performance test: 10K customer × 100K orders JOIN <5s

### Week 18: Open Source Polish
- [ ] README.md (Chinese + English bilingual):
  - [ ] Quick Start (5 minutes to first query)
  - [ ] Configuration examples (MySQL, PostgreSQL, HTTP API, two-step auth)
  - [ ] Architecture diagram
  - [ ] Demo GIF/screenshot
- [ ] 30-second demo video (e-commerce analysis scenario)
- [ ] CONTRIBUTING.md
- [ ] GitHub Actions CI:
  - [ ] Push → lint + test (ubuntu + macos + windows)
  - [ ] Tag → multi-platform build + release
- [ ] Submit PR to awesome-mcp-servers
- [ ] PyPI release: `pip install deepferry`
- [ ] Final go/no-go: all 9 acceptance scripts passed

---

## Task Summary

| Milestone | Weeks | Tasks | Acceptance Scripts |
|-----------|-------|-------|-------------------|
| M1 | 1-5 | 30+ | 9 checks (MySQL + PostgreSQL + MCP) |
| M2 | 6-9 | 20+ | 6 checks (HTTP + two-step auth) |
| M3 | 10-14 | 25+ | 7 checks (desktop app) |
| M4 | 15-18 | 20+ | 5 checks (DuckDB + polish) |
| **Total** | **18** | **95+** | **27** |
