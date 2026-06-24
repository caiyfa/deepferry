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
| M2 | HTTP API data source + two-step authentication | Week 6-9 |
| M3 | Tauri desktop table application | Week 10-14 |
| M4 | DuckDB cross-source JOIN + open source polish | Week 15-18 |

## Out of Scope (MVP)

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
