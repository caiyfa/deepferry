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

## Acceptance Criteria (M3)

1. macOS installer double-click → app launches with FastAPI sidecar auto-started
2. Select MySQL source, execute query, results render in AG Grid
3. 100K rows scroll smoothly (<100ms per scroll event)
4. Column sort and filter work correctly
5. Double-click cell → inline edit (frontend only)
6. CSV export produces valid CSV file
7. Windows installer passes same verification

## Interview Story

"Tauri vs Electron isn't just about bundle size (50MB vs 250MB) — it's about architectural clarity. Tauri's sidecar pattern maps perfectly to our Python backend: the Rust shell manages process lifecycle, the webview renders results. I avoided the trap of putting a spreadsheet engine (Univer) in — AG Grid Community is the right abstraction level for query results. Data Grid ≠ Spreadsheet."

## Dependencies

- [[mcp-server]] — Same FastAPI backend used by MCP server
- [[sql-datasource]] — Data source implementations
