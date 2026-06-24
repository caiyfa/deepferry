# deepferry — Agent Instructions

> For AI coding agents (Claude Code, Cursor, Copilot, OpenCode) working on this repository.

## Project Identity

**deepferry** is an MCP-based universal data access layer for LLM agents. Ferry = shuttling data between heterogeneous sources (MySQL, PostgreSQL, HTTP APIs) — active transport, not static bridge.

- **Repository**: `caiyfa/deepferry`
- **Language**: Python 3.12 (pure Python, no Rust rewrite)
- **License**: MIT
- **Target**: PyPI package + Tauri desktop app

## First Principles

1. **Specs before code**: Every feature starts in `openspec/specs/`. Read the relevant spec before implementing.
2. **DataSource abstraction is sacred**: All data sources implement the same ABC. Never break the interface for one implementation's convenience.
3. **Async everywhere**: No sync blocking IO. `asyncmy`, `asyncpg`, `httpx2`, `aiosqlite`. If you see `time.sleep()` or synchronous DB calls, fix them.
4. **Config is declarative**: `config.toml` is the single source of truth. Never hardcode connection strings. Support `${ENV_VAR}` injection.
5. **Errors are structured**: Agents never see Python tracebacks. Every error is a JSON object with `code`, `message`, `suggestion`.
6. **Storage is separated**: config.toml ≠ SQLite ≠ DuckDB. Never mix concerns. See `openspec/project.md` §Storage Layer.

## Repository Structure

```
openspec/              ← Spec-Driven Development artifacts (read FIRST)
├── project.md         ← North Star, architecture, tech stack
├── specs/             ← Capability specs (one per module)
│   ├── mcp-server.md
│   ├── datasource-abstraction.md
│   ├── sql-datasource.md
│   ├── http-api-datasource.md
│   ├── two-step-auth.md
│   ├── desktop-app.md
│   ├── duckdb-cross-source.md
│   └── config-panel.md
└── changes/           ← Change proposals
    └── mvp-init/      ← Current change
        ├── proposal.md
        ├── design.md
        └── tasks.md

src/deepferry/         ← Python package
├── cli.py             ← CLI entry point
├── config.py          ← Config loading
├── mcp_server/        ← MCP Server implementation
├── datasources/       ← DataSource ABC + implementations
├── auth/              ← Two-step auth (M2)
├── core/              ← Shared models, errors, SQLite helpers
└── web/               ← FastAPI config panel + sidecar API

frontend/              ← Tauri + React + AG Grid (M3)
tests/                 ← pytest + pytest-asyncio
```

## Workflow

### Before Writing Code

1. **Read the spec**: `openspec/specs/<capability>.md` — understand what, why, and acceptance criteria
2. **Read the design**: `openspec/changes/mvp-init/design.md` — understand architecture decisions
3. **Check tasks**: `openspec/changes/mvp-init/tasks.md` — find your task, mark it in progress

### While Coding

1. Follow existing patterns in `src/deepferry/`
2. Match code style: type hints everywhere, Pydantic v2 for IO boundaries, docstrings for public APIs
3. Run `ruff check .` and `mypy --strict` after every meaningful change
4. Write tests alongside code — target >70% coverage on core logic

### After Coding

1. Run full test suite: `pytest tests/ -v`
2. Verify acceptance criteria from the spec are met
3. Update `tasks.md`: mark completed
4. Do NOT commit if any acceptance criteria are unmet

## Conventions

### Python

```python
# Type hints: always
async def execute(self, query: QueryRequest) -> StructuredResult: ...

# Pydantic v2 for all IO boundaries
class QueryRequest(BaseModel):
    source_id: str
    statement: str
    params: dict[str, Any] | None = None
    timeout: int = Field(default=30, ge=1, le=300)

# Async context managers for resources
async with self._pool.acquire() as conn:
    ...

# Structured errors, never raw exceptions
raise DataSourceError(
    code="CONNECTION_FAILED",
    message=f"Cannot connect to {self._config.host}:{self._config.port}",
    suggestion="Check if the database is running and credentials are correct",
)

# Config via env vars
user = os.environ.get("MYSQL_USER", config.get("user", "root"))
```

### Testing

```python
# Use pytest-asyncio for async tests
@pytest.mark.asyncio
async def test_mysql_query(mysql_source: MySQLDataSource):
    result = await mysql_source.execute(QueryRequest(
        source_id="test-db",
        statement="SELECT 1 AS one",
    ))
    assert result.rows[0]["one"] == 1

# Use fixtures for Docker services
@pytest.fixture
async def mysql_source():
    config = MySQLConfig(host="localhost", port=3306, ...)
    source = MySQLDataSource(config)
    await source.connect()
    yield source
    await source.disconnect()
```

### TypeScript (Frontend — M3)

```typescript
// Typed API client
interface QueryResult {
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
  row_count: number;
  execution_time_ms: number;
}

// React components with explicit props
interface ResultGridProps {
  result: QueryResult;
  onExportCSV: () => void;
}
```

## Key Design Decisions (Don't Violate)

| Decision | Rule |
|----------|------|
| Pure Python | No Rust port. Use PyO3 for hotspots only if profiling proves need. |
| Three-tier storage | Config never in SQLite. Query results never cached in SQLite. DuckDB only for M4 JOIN. |
| AG Grid (not Univer) | We're a Data Grid, not a Spreadsheet. No formula engine. |
| Tauri (not Electron) | 50-80MB target. Sidecar pattern for Python backend. |
| TOML config | Not YAML, not JSON. Follow MCP ecosystem convention. |
| MIT License | All new code is MIT. No copyleft dependencies. |

## Commands

```bash
# Development
uv sync                    # Install dependencies
uv run deepferry mcp-server --config config.toml  # Start MCP server
uv run pytest tests/ -v    # Run tests
uv run ruff check .        # Lint
uv run mypy src/           # Type check

# Frontend (M3+)
cd frontend && npm install
npm run tauri dev          # Dev mode with hot reload
npm run tauri build        # Production build

# CI
# GitHub Actions: push → lint + test (ubuntu + macos + windows)
# GitHub Actions: tag → multi-platform build + release
```

## Dependencies

### Runtime (pyproject.toml)

```
mcp>=1.27,<2
fastapi[standard]>=0.138,<1
uvicorn[standard]>=0.49,<1
pydantic>=2.13,<3
asyncmy>=0.2.11,<1
asyncpg  # version locked at M1 init
SQLAlchemy[asyncio]>=2.0.51,<3
duckdb>=1.5.4,<2
httpx2[http2]>=2.4,<3
authlib>=1.7.2,<2
PyJWT>=2.13,<3
openai>=2.43,<3  # DeepSeek via base_url
aiosqlite        # version locked at M1 init
```

### Dev

```
ruff>=0.15,<1
pytest>=8,<9
pytest-asyncio>=0.24,<1
mypy>=1,<2
```

## Out of Scope (Don't Build)

- Oracle / SQL Server support (CI cost disproportionate)
- OAuth2 full authorization code flow (complexity 3x)
- RBAC / multi-tenant (personal portfolio)
- NL2SQL / Text-to-SQL (separate project)
- Excel formula engine (Data Grid ≠ Spreadsheet)
- Real-time streaming (Kafka, etc.)
- Kubernetes / Helm (single-machine deployment only)

## Related Resources

- Wiki entity: `stu_llm_wiki/wiki/entities/deepferry.md`
- MVP scope: `stu_llm_wiki/wiki/queries/2026-06-23-mvp-scope-lock.md`
- Tech design: `stu_llm_wiki/wiki/queries/2026-06-23-1853-agent-data-access-tool-design.md`
- Naming decision: `stu_llm_wiki/wiki/queries/2026-06-24-0920-deepferry-naming.md`
- MCP protocol: https://modelcontextprotocol.io
- DuckDB: https://duckdb.org
- Tauri: https://tauri.app
