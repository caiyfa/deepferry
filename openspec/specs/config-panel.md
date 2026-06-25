# Capability: Web UI Configuration Panel

> **Status**: planned | **Milestone**: M1 (partial) → M3 (complete) | **Owner**: full-stack

## Summary

A lightweight web-based configuration panel embedded in FastAPI for managing data source connections. CRUD operations on data sources, connection testing, and query history browsing.

## Motivation

- `config.toml` is great for power users but intimidating for new users
- Web UI provides visual feedback for connection testing (green check / red X)
- Query history browsing enables debugging and performance analysis

## Specification

### Pages

| Page | Route | Purpose |
|------|-------|---------|
| Dashboard | `/` | Overview: source count, recent queries, system health |
| Sources | `/sources` | List, add, edit, delete data source configs |
| Source Detail | `/sources/:id` | Test connection, view schema, run sample query |
| Query History | `/history` | Paginated list of past queries with filters |
| Settings | `/settings` | Global config (log level, port, etc.) |

### Source CRUD

```
POST   /api/config/sources          # Add new source
GET    /api/config/sources          # List all sources
GET    /api/config/sources/:id      # Get source details
PUT    /api/config/sources/:id      # Update source config
DELETE /api/config/sources/:id      # Remove source
POST   /api/config/sources/:id/test # Test connection
```

### Source Governance (Mode-Dependent)

Who may add/edit/delete sources depends on the operating mode (see
[[project]] § Operating Modes):

| Mode | Who can mutate sources | Approval | Credential entry |
|------|------------------------|----------|------------------|
| Desktop | Single local user (no RBAC) | Immediate (one-click) | OS keychain (see [[desktop-app]] § Credential Storage) |
| Server (production) | **Admin only** (RBAC) | Required — changes go through a review/deploy window | Secrets backend (env / Vault); never plaintext in `config.toml` |

In server mode the config-panel Web UI is the administrative surface for data
source lifecycle. Non-admin users may browse and query, but cannot create,
edit, or delete source connections. Every mutation is recorded in the audit
trace (who/when/what-changed) alongside query spans.

**Hot reload.** A successful CRUD write updates `config.toml` atomically
(write-to-temp + rename) and triggers `registry.refresh()` — the new/updated
source becomes queryable on the next `query()` call, with no process restart.
In-flight queries complete against the source instance they started on; see
[[datasource-abstraction]] § Registry Lifecycle for the refresh contract.

### Connection Test

```python
@router.post("/api/config/sources/{source_id}/test")
async def test_connection(source_id: str) -> TestResult:
    source = registry.get(source_id)
    health = await source.health_check()
    return TestResult(
        ok=health.ok,
        latency_ms=health.latency_ms,
        error=health.error,
        details={
            "type": source.type,
            "host": source.host if hasattr(source, 'host') else source.base_url,
        }
    )
```

### Query History (SQLite-backed)

```python
@router.get("/api/history")
async def query_history(
    source_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedHistory:
    rows = await db.fetch(
        "SELECT * FROM query_history WHERE ... ORDER BY created_at DESC LIMIT ? OFFSET ?",
        limit, offset
    )
    return PaginatedHistory(rows=rows, total=total, limit=limit, offset=offset)
```

History table schema (see `project.md` Storage Layer):

```sql
CREATE TABLE query_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    query_text  TEXT NOT NULL,
    row_count   INTEGER,
    duration_ms REAL,
    status      TEXT NOT NULL,  -- 'success' | 'error'
    error_msg   TEXT,
    created_at  INTEGER NOT NULL
);
```

### Technology

- FastAPI serves both API and static HTML/CSS/JS
- Minimal JS — no React/Vue framework for config panel (keep it simple)
- htmx or vanilla JS for interactivity (form submission, status toggles)
- CSS: simple utility classes, no framework needed

## Acceptance Criteria

1. Add a MySQL source via Web UI → written to `config.toml` correctly
2. Test connection button → green check for valid, red X with message for invalid
3. Edit source → update `config.toml` → agent picks up changes on next query
4. Delete source → removed from config, all queries for that source remain in history
5. Query history pagination works (50 per page)
6. Filter history by source and status

## Dependencies

- [[datasource-abstraction]] — Health check, source registry
- [[sql-datasource]] — Test targets
