# Capability: Query Enhancement

> **Status**: planned | **Milestone**: M3 | **Owner**: frontend | **Depends on**: `console-shell`

## Summary

Upgrade the existing SQL query page with a persistent Schema panel, intelligent
autocomplete, saved queries with parameterization, result pinning for comparison,
and AI-powered SQL analysis.

## Motivation

- The current query page is a bare textarea + execute button. Schema discovery
  requires switching to a different tab or running `SHOW TABLES` manually.
- Power users who DO write SQL need IDE-grade tooling: autocomplete, formatting,
  parameterized templates, result comparison.
- Saved queries are requested by users who run the same analysis repeatedly
  (weekly reports, monthly snapshots).
- AI optimization helps less SQL-proficient users improve their queries.

## Specification

### Split Layout

```
┌─ SQL Editor (flex: 1) ────────┬─ Schema Panel (260px) ──┐
│  Line numbers + syntax highlight │  Source tree (collapsible)│
│  [Execute] [Format] [AI] [Save]  │  Table → columns → types  │
│                                  │  Click → insert into SQL  │
│  Result table (expands below)    │                           │
│  [Chart] [CSV] [Pin] [Compare]   │                           │
└──────────────────────────────────┴───────────────────────────┘
```

### Schema Panel

Refactored from the current non-existent sidebar to a right-side panel:

- Tree view of tables per data source
- Click table name → expand column list with types
- Click column name → insert `table.column` at cursor in SQL editor
- Click table name header → insert `SELECT * FROM table` template
- Sticky position, scrolls independently from the editor
- Search bar at top: filter tables/columns by name
- When multi-source selected, shows per-source sections with relationship hints
  (→ `cross-source-ui.md`)

### SQL Editor Enhancement

- **Syntax highlighting**: SQL keywords (blue/purple), strings (green), numbers
  (yellow), comments (gray)
- **Line numbers**: gutter with line count
- **Autocomplete** (`Ctrl+Space`):
  - After `FROM` → list of tables from selected source(s)
  - After `table.` → list of columns for that table
  - After `JOIN` → list of tables with join hints
  - SQL keywords always available
- **Format** button: normalize indentation, uppercase keywords
- **Error highlighting**: when query fails, highlight the problematic line

### Saved Queries

Storage: SQLite table in the existing deepferry database.

```sql
CREATE TABLE IF NOT EXISTS saved_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    statement TEXT NOT NULL,
    source_ids TEXT NOT NULL,  -- JSON array of source IDs
    params TEXT,               -- JSON array of parameter names
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_executed_at TIMESTAMP
);
```

- **Save**: `Ctrl+S` on the query page → modal with name + description
- **Load**: click from sidebar "Saved" section → restores SQL + selects sources
- **Delete**: right-click or context menu on saved item
- **Rename**: double-click name in sidebar

### Parameterized Queries

Template syntax: `{{param_name}}`

```sql
SELECT * FROM customers
WHERE status = {{status}}
  AND created_at >= {{start_date}}
ORDER BY {{order_col}} {{order_dir}}
```

- Execute → modal pops up with input fields for each `{{param}}`
- Input types inferred from context:
  - `= {{x}}` → text input
  - `>= {{date}}` → date picker
  - `IN ({{ids}})` → multi-value input
  - `{{col}}` in ORDER BY → dropdown from table columns
- Saved queries retain their parameter definitions

### Result Pinning + Comparison

After a query executes, user can "Pin" the result:

- Pinned results appear as tabs below the editor
- Up to 5 results can be pinned simultaneously
- Click "Compare" → side-by-side or stacked diff view
- Diff uses DuckDB for row-level comparison when both results are from
  the same schema (→ `dataset-engine.md` diff engine)
- Each pinned result shows: query time, row count, "X minutes ago"
- Unpin individually or "Clear all"

### AI SQL Optimization

Button in the editor toolbar triggers analysis of the current SQL:

- Calls LLM (via `llm-integration.md`) with a specialized prompt for query analysis
- Returns:
  1. **Performance**: index suggestions, JOIN order, subquery optimization
  2. **Safety**: confirmation that no dangerous operations detected
  3. **Readability**: formatting suggestions, comment recommendations
- Results shown in a slide-out panel, not replacing the editor
- "Apply" button for formatting suggestions only (performance changes are
  advisory, not auto-applied)

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/saved-queries` | GET | List saved queries |
| `/api/saved-queries` | POST | Save a query |
| `/api/saved-queries/:id` | PUT | Update a saved query |
| `/api/saved-queries/:id` | DELETE | Delete a saved query |
| `/api/query/analyze` | POST | AI analysis of a SQL statement |

## Acceptance Criteria

- [ ] Click column name in Schema panel → `customers.name` inserted at cursor in SQL editor
- [ ] Type `FROM ` → autocomplete shows table list; select one → name inserted
- [ ] `Ctrl+S` → save modal → query appears in sidebar "Saved" section
- [ ] Execute `SELECT * FROM customers WHERE status = {{s}}` → parameter modal with text input
- [ ] Pin result A, execute new query, pin result B → "Compare" shows diff
- [ ] Click "AI" → analysis panel shows index suggestion for unindexed JOIN column
- [ ] Saved query persists across page reloads
- [ ] Format button capitalizes SQL keywords and aligns indentation

## Out of Scope

- Visual query builder (drag-and-drop table JOINs)
- Query version history (diff between edits of the same saved query)
- Sharing saved queries via URL (security review needed)
- Multi-tab query editor (one SQL at a time)
- Export query results to BI tools (Tableau, PowerBI connectors)
