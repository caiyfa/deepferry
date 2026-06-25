# Capability: Desktop Table Application

> **Status**: planned | **Milestone**: M3 | **Owner**: frontend

## Summary

A cross-platform (Win + Mac) desktop application using Tauri 2.x + React + TypeScript + AG Grid Community. Displays query results in a virtual-scrolled data grid with sorting, filtering, inline editing, and CSV export.

## Motivation

- LLM agents are one entry point; humans need a second — a visual data browser
- Desktop app provides richer UX than a browser tab (file system access, native menus, sidecar management)
- Differentiates from pure-MCP tools that have no visual interface

## Specification

### Architecture: Tauri Sidecar Pattern

```
┌─────────────────────────────────────────┐
│           Tauri Desktop Shell             │
│  ┌───────────────────────────────────┐   │
│  │     React Frontend (WebView)       │   │
│  │     · AG Grid Community            │   │
│  │     · Query input + result display │   │
│  │     · Sort / Filter / Edit / Export│   │
│  └──────────────┬────────────────────┘   │
│                 │ HTTP (localhost:8080)    │
│  ┌──────────────▼────────────────────┐   │
│  │     FastAPI Sidecar (Python)       │   │
│  │     · /api/datasources             │   │
│  │     · /api/query                   │   │
│  │     · /api/history                 │   │
│  └────────────────────────────────────┘   │
│                                           │
│  Tauri manages sidecar lifecycle:         │
│  · Start Python process on app launch     │
│  · Health check: GET /api/health          │
│  · Kill on app close                      │
└─────────────────────────────────────────┘
```

### Sidecar Lifecycle

```rust
// Tauri sidecar configuration (tauri.conf.json)
{
  "bundle": {
    "externalBin": ["binaries/python-sidecar"]
  }
}
```

- Dev mode: `uvicorn --reload` (hot reload) + `npm run tauri dev`
- Production: PyInstaller bundles Python + FastAPI into single binary (40-60MB)
- Total package: 50-80MB (vs Electron 150-250MB)

### Frontend Routes

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `DataSourceSelector` | Select data source + write query |
| `/results` | `QueryResults` | AG Grid display of results |
| `/history` | `QueryHistory` | Past queries with metadata |
| `/history/:id` | `ExecutionDetail` | Trace timeline + expandable spans for one execution (see [[audit-trace]]) |
| `/scenarios` | `ScenarioList` | All investigation scenarios with query counts; drill into one to see its grouped queries side-by-side |
| `/scenarios/:id` | `ScenarioDetail` | All queries in one scenario: timeline + result comparison (multi-tab / split view) |
| `/settings` | `ConfigEditor` | Edit config.toml (advanced) |

### AG Grid Features

| Feature | Description |
|---------|-------------|
| Virtual scrolling | 100K+ rows smooth (<100ms scroll response) |
| Column sorting | Click column header to sort ASC/DESC |
| Column filtering | Text/number/date filters per column |
| Inline editing | Double-click cell to edit (frontend only, no DB write in MVP) |
| Column resize | Drag column borders |
| Row selection | Click to select, Ctrl+Click for multi-select |
| CSV export | Button → download `results.csv` |

### API Endpoints (FastAPI Sidecar)

```python
@router.get("/api/datasources")
async def list_datasources() -> list[SourceInfo]:
    """List all configured data sources with health status."""

@router.post("/api/query")
async def execute_query(req: QueryRequest) -> StructuredResult:
    """Execute a query against a data source."""

@router.get("/api/history")
async def query_history(limit: int = 50, offset: int = 0) -> list[HistoryEntry]:
    """Get query history from SQLite."""

@router.get("/api/health")
async def health() -> dict:
    """Health check for Tauri sidecar management."""

@router.get("/api/schema/{source_id}")
async def get_schema(source_id: str) -> Schema:
    """Get schema metadata for a data source."""

@router.get("/api/executions")
async def list_executions(
    source_id: str | None = None, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> PaginatedExecutions:
    """List execution traces (see [[audit-trace]])."""

@router.get("/api/executions/{execution_id}")
async def get_execution(execution_id: int) -> ExecutionWithSpans:
    """One execution + its nested span tree."""

@router.get("/api/executions/{execution_id}/spans/{span_id}")
async def get_span(execution_id: int, span_id: int) -> SpanDetail:
    """Single span detail (attributes expanded, payload excerpt if captured)."""

@router.get("/api/scenarios")
async def list_scenarios(
    session_id: str | None = None, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> PaginatedScenarios:
    """List investigation scenarios (see [[audit-trace]])."""

@router.get("/api/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str) -> ScenarioWithQueries:
    """One scenario + all its queries (executions) for grouped review."""

@router.post("/api/scenarios")
async def create_scenario(label: str | None = None, session_id: str | None = None) -> Scenario:
    """Open a scenario (also exposed as the MCP `start_scenario` tool)."""

@router.post("/api/scenarios/{scenario_id}/close")
async def close_scenario(scenario_id: str) -> Scenario:
    """Close a scenario (also exposed as the MCP `end_scenario` tool)."""
```

### Frontend State (SQLite/ui.db)

```sql
-- Tauri Rust side manages this independently
CREATE TABLE ui_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
-- Stores: recent sources, recent queries, column widths, sort preferences, favorites
```

### Credential Storage

Desktop mode runs on a single developer's machine; credentials belong to the
user, not the organization. They are stored in the **OS keychain**, never in
`config.toml` plaintext and never in the LLM context.

| Platform | Backend |
|----------|---------|
| macOS | Keychain (via `keyring` lib) |
| Windows | Credential Manager |
| Linux | Secret Service (GNOME Keyring / KWallet) |

Flow:

1. User adds a source via the desktop UI (or confirms an agent
   `propose_source`). The topology (type/host/port/db) is written to
   `config.toml`; the password is written to the keychain under a service
   entry like `deepferry/<source_id>`.
2. On sidecar startup, the Python process resolves `${ENV_VAR}` references by
   reading the keychain and injecting values into the process environment —
   `DataSource` instances then consume them via the standard `${ENV_VAR}`
   mechanism (see [[datasource-abstraction]]).
3. Credentials never traverse the LLM: `propose_source` carries topology only
   (no password field); audit spans redact `password|secret|token|api_key`
   (see [[audit-trace]]).

This mirrors the established pattern in DBeaver / TablePlus / DataGrip, where
single-user tools store secrets in the OS keychain by default.

## Acceptance Criteria (M3)

1. macOS installer double-click → app launches with FastAPI sidecar auto-started
2. Select MySQL source, execute query, results render in AG Grid
3. 100K rows scroll smoothly (<100ms per scroll event)
4. Column sort and filter work correctly
5. Double-click cell → inline edit (frontend only)
6. CSV export produces valid CSV file
7. Windows installer passes same verification
8. `/history/:id` renders a timeline of spans in execution order with depth-based indentation; each span row expands to show kind, duration, status, and redacted attribute summaries
9. An `auth_retry` span renders with a distinct (amber) status color vs success (green) / error (red); clicking it reveals the trigger status and attempt count
10. A 50-span execution renders in <500ms (virtualized list, no charting library)

## Interview Story

"Tauri vs Electron isn't just about bundle size (50MB vs 250MB) — it's about architectural clarity. Tauri's sidecar pattern maps perfectly to our Python backend: the Rust shell manages process lifecycle, the webview renders results. I avoided the trap of putting a spreadsheet engine (Univer) in — AG Grid Community is the right abstraction level for query results. Data Grid ≠ Spreadsheet."

## Dependencies

- [[mcp-server]] — Same FastAPI backend used by MCP server
- [[sql-datasource]] — Data source implementations
