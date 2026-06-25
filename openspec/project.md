# deepferry — Project Overview

> **North Star**: Let any LLM Agent query MySQL, PostgreSQL, and HTTP APIs through a single MCP protocol — including APIs requiring two-step authentication — and view results in a desktop table application.

## Identity

| Field | Value |
|-------|-------|
| Product | deepferry |
| Meaning | ferry = shuttling data between heterogeneous sources (active transport, not static bridge) |
| Repository | `caiyfa/deepferry` |
| License | MIT |
| Target Role | DeepSeek Agent Engineer |
| Timeline | 16.5 weeks (4 milestones) |

## Operating Modes

deepferry serves two deployment scenarios off one shared core. The modes
differ in **who provisions data sources, how credentials are stored, and what
the agent is allowed to do** — not in the query path itself.

| Mode | Scenario | User | Credentials | Agent source provisioning |
|------|----------|------|-------------|---------------------------|
| **Desktop** (Tauri sidecar) | Dev/Ops ad-hoc data triage — fast, exploratory | Single developer (local) | OS keychain (macOS Keychain / Windows Credential Manager / Linux Secret Service) | `propose_source` tool exposed; agent proposes topology, user one-click confirms |
| **Server** (MCP over Streamable HTTP) | Production reporting — stable, governed | Team / org (multi-user) | Secrets backend (env / Vault / KMS) | No provisioning tool; admin-only via config-panel Web UI |

Both modes share the `DataSource` ABC, `config.toml` schema, and the
`${ENV_VAR}` credential injection layer — only the credential *source* and the
agent's provisioning capability differ. See [[mcp-server]] § Operating Modes,
[[config-panel]] § Source Governance, [[desktop-app]] § Credential Storage.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Entry Layer (Dual-Modal)              │
│  ┌──────────────┐                    ┌───────────────┐  │
│  │  MCP Server  │                    │ Tauri Desktop  │  │
│  │  (stdio/HTTP)│                    │ (React+AG Grid)│  │
│  └──────┬───────┘                    └───────┬───────┘  │
│         │                                    │          │
│         │         ┌──────────────┐           │          │
│         └────────►│  FastAPI Core │◄─────────┘          │
│                   │  (async routes)                     │
│                   └───────┬───────┘                     │
│                           │                             │
│   ┌───────────────────────┼───────────────────────┐     │
│   ▼                       ▼                       ▼     │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐│
│ │ MySQL    │ │PostgreSQL│ │ HTTP API │ │ DuckDB Engine ││
│ │DataSource│ │DataSource│ │DataSource│ │ (cross-source)││
│ └────┬─────┘ └────┬─────┘ └────┬─────┘ │   M4 phase    ││
│      │            │            │        └──────────────┘│
│      ▼            ▼            ▼                        │
│ ┌──────────────────────────────────────────────────────┐│
│ │              DataSource Abstract Base                 ││
│ │  · execute(query) → StructuredResult                 ││
│ │  · list_resources() → [Resource]                     ││
│ │  · schema_info() → Schema                            ││
│ └──────────────────────────────────────────────────────┘│
│                                                         │
│ ┌──────────────────────────────────────────────────────┐│
│ │              Storage Layer (3-tier separation)        ││
│ │  · Config:  config.toml (declarative, git-diffable)  ││
│ │  · Runtime: SQLite/app.db (query history/token/meta) ││
│ │  · Analytics: DuckDB (M4 cross-source JOIN only)     ││
│ │  · Frontend: SQLite/ui.db (Tauri UI state)           ││
│ └──────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.12 |
| Protocol | MCP (Python SDK) | ≥1.27, <2 |
| Web Framework | FastAPI + Uvicorn | ≥0.138 |
| Data Validation | Pydantic | ≥2.13, <3 |
| MySQL Driver | asyncmy | ≥0.2.11 |
| PostgreSQL Driver | asyncpg | TBD at M1 init |
| ORM | SQLAlchemy[asyncio] | ≥2.0.51 |
| Cross-Source Query | DuckDB | ≥1.5.4 |
| HTTP Client | httpx2[http2] | ≥2.4 |
| Auth | authlib + PyJWT | ≥1.7.2 / ≥2.13 |
| Desktop Shell | Tauri | 2.x |
| Frontend | React + TypeScript + AG Grid Community | 35.x |
| Package Manager | uv | ≥0.11 |
| Lint/Format | ruff | ≥0.15 |
| Test | pytest + pytest-asyncio | ≥8 / ≥0.24 |
| LLM Backend | openai SDK → DeepSeek | ≥2.43 |

## Milestones

| Milestone | Contents | Timeline |
|-----------|----------|----------|
| M1 | MCP Server + MySQL + PostgreSQL + DataSource abstraction | Week 1-5 |
| M2 | HTTP API data source + two-step authentication (incl. reactive 401) | Week 6-9 |
| M2.5 | Orchestration engine + execution trace & audit + scenario correlation | Week 9-11 |
| M3 | Tauri desktop table application (incl. execution detail + scenario views) | Week 11-15 |
| M4 | Cross-source aggregation (production-grade DuckDB federation) + open source polish | Week 15-18 |

### v1 Production Posture

The first version ships **fewer features, each production-grade** — not a demo.
The guiding rules:

- **No demo escape hatches**: the pandas cross-source fallback, sampling-only
  schema inference, and "30-second showcase" framing are removed. Failures
  surface as structured errors, never masked.
- **Cross-source aggregation is v1 core**, not a demo finale — it is the
  product's reason to exist (recombine data across sources into reports).
- **Production safeguards are mandatory on every data path**: read-only
  enforcement, statement timeouts, row caps, streaming cursors, pool ceilings,
  filter-pushdown enforcement, and memory bounding. See [[sql-datasource]],
  [[http-api-datasource]], [[duckdb-cross-source]].
- **Auditability is non-optional**: every query produces a span tree; every
  investigation can be grouped into a scenario and reviewed step-by-step. See
  [[audit-trace]].

### Out of Scope (MVP)

- Oracle / SQL Server (CI cost disproportionate)
- OAuth2 full authorization code flow (complexity 3x)
- RBAC / multi-tenant (personal portfolio)
- NL2SQL / Text-to-SQL (separate project)
- Excel formula engine (Data Grid ≠ Spreadsheet)
- Real-time streaming sources (Kafka, etc.)

## Interview Story Map

| Decision | Interview Narrative |
|----------|-------------------|
| MCP tools (not resources) | Three-primitive trade-off analysis |
| DataSource abstraction | Unified interface across schema / schema-less sources |
| Two-step auth | Stateful session injection into stateless tool calls |
| Tauri (not Electron) | 50MB vs 250MB, Rust safety |
| SQLite boundaries | Declarative vs imperative, OLTP vs OLAP |
| Naming "deepferry" | Six-dimension uniqueness check (PyPI/crates/npm/GitHub/domain/trademark) |

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Pure Python (not Rust) | IO-bound, 95% time in C extensions, Python glue <5% |
| Three-tier storage separation | config.toml (declarative) / SQLite (append-only) / DuckDB (columnar) |
| Config as YAML/TOML (not SQLite) | vim-editable, git-diffable, MCP ecosystem convention |
| Async throughout | asyncmy / asyncpg / httpx2 / FastAPI async — no sync blocking |
| Type-safe boundaries | Pydantic v2 for all IO, ruff + mypy --strict |
