# Change Proposal: MVP Initialization

> **Change ID**: `mvp-init`
> **Status**: proposed
> **Created**: 2026-06-24
> **Timeline**: 16.5 weeks (4 milestones)

## Why

LLM agents need to query real-world data, but existing tools are fragmented:

- **Agent-side**: MCP database tools exist (dbhub, dbmcp), but only for SQL databases — no HTTP API support
- **Human-side**: BI tools (Superset, Metabase) have visual dashboards, but don't expose MCP tools for agents
- **Auth gap**: No tool supports two-step authentication (login → token → query) for HTTP APIs

**deepferry** bridges this gap: one protocol (MCP) to query SQL databases and HTTP APIs, with a human-facing desktop app as a second entry point, and two-step auth as the key differentiator.

## What Changes

### ADDED Capabilities

| Capability | Spec | Milestone |
|-----------|------|-----------|
| MCP Server (stdio + HTTP) | `specs/mcp-server.md` | M1 |
| DataSource Abstraction | `specs/datasource-abstraction.md` | M1 |
| MySQL Data Source | `specs/sql-datasource.md` | M1 |
| PostgreSQL Data Source | `specs/sql-datasource.md` | M1 |
| HTTP API Data Source | `specs/http-api-datasource.md` | M2 |
| Two-Step Authentication | `specs/two-step-auth.md` | M2 |
| Desktop App (Tauri + AG Grid) | `specs/desktop-app.md` | M3 |
| Web Config Panel | `specs/config-panel.md` | M1/M3 |
| DuckDB Cross-Source JOIN | `specs/duckdb-cross-source.md` | M4 |
| PyPI Packaging | (included in M4) | M4 |

### Project Skeleton

```python
src/deepferry/
├── __init__.py
├── cli.py                    # CLI entry: deepferry mcp-server
├── config.py                 # Config loading (toml, env vars)
├── mcp_server/
│   ├── __init__.py
│   ├── server.py             # MCP Server setup (stdio + HTTP)
│   └── tools.py              # Tools: list_tables, query, schema_info
├── datasources/
│   ├── __init__.py
│   ├── base.py               # DataSource ABC
│   ├── registry.py           # Source registry + discovery
│   ├── mysql.py              # MySQL implementation
│   ├── postgresql.py         # PostgreSQL implementation
│   └── http_api.py           # HTTP API implementation (M2)
├── auth/
│   ├── __init__.py
│   ├── token_manager.py      # Token cache + refresh + concurrency (M2)
│   └── models.py             # Auth config models
├── core/
│   ├── __init__.py
│   ├── models.py             # Shared Pydantic models
│   ├── errors.py             # Structured error types
│   └── db.py                 # SQLite helpers (query history, token cache)
└── web/
    ├── __init__.py
    ├── app.py                # FastAPI app (config panel + sidecar API)
    ├── routes/
    │   ├── config.py         # Source CRUD
    │   ├── query.py          # Query execution
    │   └── history.py        # Query history
    └── static/               # HTML/CSS/JS for config panel

frontend/
├── src/
│   ├── App.tsx
│   ├── components/
│   │   ├── DataSourceSelector.tsx
│   │   ├── QueryEditor.tsx
│   │   ├── ResultGrid.tsx     # AG Grid wrapper
│   │   └── HistoryPanel.tsx
│   └── api/                   # FastAPI client
├── src-tauri/                 # Tauri Rust shell
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   └── src/main.rs            # Sidecar lifecycle
├── package.json
└── tsconfig.json

tests/
├── test_mcp_server.py
├── test_datasource_abstraction.py
├── test_mysql_source.py
├── test_postgresql_source.py
├── test_http_source.py
├── test_two_step_auth.py
├── conftest.py                # Fixtures: Docker MySQL, PG, mock auth server
└── integration/

pyproject.toml
config.example.toml
README.md
```

## Impact

- **New project** (greenfield) — no existing code affected
- **Repository**: `caiyfa/deepferry` (independent from wiki)
- **License**: MIT
- **Target**: Python 3.12, published to PyPI as `deepferry`

## Risks

| Risk | Mitigation |
|------|-----------|
| DuckDB JSON materialization complex | Degradation to Python-level pandas join |
| Tauri cross-platform packaging issues | M3 week 1: ship minimal sidecar hello world first |
| Two-step auth concurrency bugs | asyncio.Lock + thorough concurrent tests |
| Timeline overrun | Per-milestone go/no-go; cut M4 scope if needed |

## Approval

- [ ] Scope confirmed (PostgreSQL included in M1)
- [ ] Timeline accepted (16.5 weeks)
- [ ] Tech stack locked
