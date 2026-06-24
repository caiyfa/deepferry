# deepferry

> 🚧 MVP under development — see [openspec/project.md](openspec/project.md) for the full plan.

**deepferry** lets any LLM Agent query MySQL, PostgreSQL, and HTTP APIs through a single MCP protocol — including APIs requiring two-step authentication — and view results in a desktop table application.

> **ferry** = shuttling data between heterogeneous sources. Active transport, not static bridge.

## Quick Links

- [Project Overview](openspec/project.md) — North Star, architecture, tech stack
- [Capability Specs](openspec/specs/) — Detailed specs for each module
- [MVP Change Proposal](openspec/changes/mvp-init/proposal.md) — What we're building and why
- [Technical Design](openspec/changes/mvp-init/design.md) — Architecture decisions
- [Implementation Tasks](openspec/changes/mvp-init/tasks.md) — Task breakdown by milestone

## Status

| Milestone | Timeline | Status |
|-----------|----------|--------|
| M1: MCP + MySQL + PostgreSQL | Week 1-5 | 🔴 Planned |
| M2: HTTP API + Two-Step Auth | Week 6-9 | 🔴 Planned |
| M3: Desktop App | Week 10-14 | 🔴 Planned |
| M4: DuckDB JOIN + Polish | Week 15-18 | 🔴 Planned |

## Tech Stack

Python 3.12 · MCP · FastAPI · asyncmy/asyncpg · SQLAlchemy · DuckDB · httpx2 · Tauri 2.x · React · AG Grid · uv · ruff

## License

MIT
