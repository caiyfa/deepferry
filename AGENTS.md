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
│   ├── orchestration.md
│   ├── audit-trace.md
│   ├── custom-datasource.md
│   └── config-panel.md
└── changes/           ← Change proposals
    ├── mvp-init/      ← Initial MVP baseline
    │   ├── proposal.md
    │   ├── design.md
    │   └── tasks.md
    └── orchestration-audit/  ← Orchestration + audit + production hardening
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

## Docker Environment & Testing Workflow

> **CRITICAL**: This section defines the canonical Docker/MySQL startup, reset, and testing workflow. All agents MUST follow this sequence exactly. Deviation causes non-reproducible test failures.

### Architecture

```
deepferry-mysql (mysql:8, port 3306)
├── deepferry          ← e2e test tables + schema_version
└── finance_ledger     ← financial-ledger-mock JPA tables

deepferry-postgres (postgres:16, port 5432)
└── deepferry          ← PostgreSQL test database

deepferry (Python MCP server, port 8000) ──→ deepferry (MySQL) + deepferry (PostgreSQL)
financial-ledger-mock (Spring Boot, port 8080) ──→ finance_ledger (user: finance)
```

One MySQL container serves both `deepferry` and `financial-ledger-mock` via different databases. The container runs on the `deepferry_deepferry` bridge network. The financial-ledger-mock compose joins this network as `external: true`.

**Config mount**: Inside Docker, `config.docker.toml` is mounted as `config.toml` with explicit environment variables (not host pass-through). Local development uses `config.toml` directly (pointing at `127.0.0.1:3306`). The Docker compose sets `MYSQL_HOST=mysql`, `POSTGRES_HOST=postgres`, etc. so the container resolves service names.

### Versioned Init System

MySQL Docker entrypoint executes `docker/mysql-init/*.sql` in alphabetical order **only when the data volume is empty** (first run or after `down -v`).

**Version tracking**: A `schema_version` table in the `deepferry` database records every applied migration:

```sql
SELECT * FROM deepferry.schema_version;
-- +---------+------------------+---------------------+
-- | version | description      | applied_at          |
-- +---------+------------------+---------------------+
-- | V001    | Baseline test... | 2026-06-29 10:00:00 |
-- | V002    | Finance Ledger.. | 2026-06-29 10:00:01 |
-- +---------+------------------+---------------------+
```

Each `Vxxx__*.sql` file MUST end with an `INSERT IGNORE` into `schema_version` to make it traceable.

### Adding a New Migration

When test data or schema needs to change:

1. Create `docker/mysql-init/V003__<short_description>.sql`
2. Make all DDL idempotent (`CREATE TABLE IF NOT EXISTS`, `INSERT ... ON DUPLICATE KEY UPDATE`)
3. Append version record:
   ```sql
   USE deepferry;
   INSERT IGNORE INTO schema_version (version, description)
   VALUES ('V003', 'What this migration adds');
   ```
4. Destroy and recreate to verify:
   ```bash
   docker compose down -v
   docker compose up mysql -d --wait
   docker compose exec mysql mysql -uroot -ptestpass deepferry \
     -e "SELECT * FROM schema_version;"
   ```
5. Run the full test suite against the fresh environment

### Startup Sequence (Development)

```bash
# 1. Start shared MySQL (always first)
docker compose up mysql -d --wait

# 2. Verify MySQL is ready
docker compose exec mysql mysqladmin ping -h localhost

# 3. Option A: Run deepferry with full profile (MySQL + PostgreSQL + app)
docker compose --profile full up -d --wait

# 3. Option B: Run only deepferry app (MySQL must already be running)
docker compose up deepferry -d

# 4. Start financial-ledger-mock (if needed)
cd examples/financial-ledger-mock
docker compose up --build -d
```

**financial-ledger-mock build note**: The Dockerfile uses `eclipse-temurin:21-jdk-jammy` and Maven. In China, configure Maven mirror via `settings-docker.xml` (included). Docker registry mirror in `~/.docker/daemon.json` is also required (see Troubleshooting).

### Development Testing

**Quick iteration** (no schema changes, MySQL already running with data):

```bash
pytest tests/ -v
ruff check . && mypy src/
```

**Full reset** (schema or seed data changed, or before acceptance testing):

```bash
# 1. Destroy everything including volumes
docker compose down -v
cd examples/financial-ledger-mock && docker compose down -v && cd ../..

# 2. Fresh MySQL with re-initialized data
docker compose up mysql -d --wait

# 3. Verify version table
docker compose exec mysql mysql -uroot -ptestpass deepferry \
  -e "SELECT version, description FROM schema_version ORDER BY version;"

# 4. Run tests
pytest tests/ -v

# 5. Lint
ruff check . && mypy src/
```

### Acceptance Testing Checklist

Before claiming a feature is complete, the agent MUST:

- [ ] `docker compose down -v` (clean state)
- [ ] `docker compose up mysql -d --wait` (MySQL healthy)
- [ ] `docker compose exec mysql mysql -uroot -ptestpass deepferry -e "SELECT COUNT(*) = 2 FROM schema_version;"` → 1 (both V001 and V002 applied)
- [ ] `docker compose exec mysql mysql -uroot -ptestpass deepferry -e "SELECT COUNT(*) = 7 FROM customers;"` → 7 (seed data intact)
- [ ] `docker compose exec mysql mysql -uroot -ptestpass -e "SHOW DATABASES;" | grep finance_ledger` → exists
- [ ] `docker compose --profile full up -d --wait` (all services healthy: deepferry, mysql, postgres)
- [ ] `curl -sf http://localhost:8000/health` → `{"status":"ok"}`
- [ ] `pytest tests/ -v` → all pass
- [ ] `ruff check .` → clean
- [ ] `mypy src/` → clean

### Self-Contained Verification

One-liner to verify the entire Docker environment is healthy:

```bash
docker compose down -v \
  && docker compose up mysql -d --wait \
  && docker compose exec mysql mysql -uroot -ptestpass deepferry \
       -e "SELECT version, description, applied_at FROM schema_version ORDER BY version;" \
  && docker compose exec mysql mysql -uroot -ptestpass -e "SHOW DATABASES;" \
  && docker compose --profile full up -d --wait \
  && curl -sf http://localhost:8000/health \
  && pytest tests/ -v
```

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Tests fail with "table not found" | MySQL volume not re-initialized after schema change | `docker compose down -v && docker compose up mysql -d --wait` |
| `schema_version` has wrong count | Old volume persisted, new migration not applied | Same as above |
| `finance_ledger` DB missing | V002 not executed (old volume or init script name changed) | Verify `docker/mysql-init/` file list; reset with `down -v` |
| financial-mock can't connect | finance user not created or network mismatch | Verify `deepferry_deepferry` network exists; check mysql health |
| financial-mock build fails: Maven `Remote host terminated` | Maven Central blocked | `settings-docker.xml` included (Aliyun mirror); rebuild with `--no-cache` |
| financial-mock build fails: `eclipse-temurin` pull fails | Docker Hub blocked | Configure `registry-mirrors` in `~/.docker/daemon.json`; restart Docker Desktop |
| deepferry MCP server unhealthy | Config points to `127.0.0.1` inside container | Must use `config.docker.toml` (mounted via compose); check `docker compose config` |
| PostgreSQL source connection refused | deepferry started before postgres was ready | `docker compose --profile full up -d --wait` handles ordering; manually restart: `docker compose restart deepferry` |

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
