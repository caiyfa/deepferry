# Capability: Cross-Source Orchestration UI

> **Status**: planned | **Milestone**: M4 | **Owner**: frontend | **Depends on**: `console-shell`, `duckdb-cross-source`, `dataset-engine`

## Summary

A visual layer that makes deepferry's heterogeneous data source orchestration
visible and explorable. When multiple data sources are queried together, the UI
shows an orchestration flow diagram (source → source → DuckDB JOIN), a
per-source timing breakdown, and cross-source schema relationship hints.

## Motivation

- The DuckDB cross-source engine (`duckdb-cross-source.md`) is the most
  architecturally unique part of deepferry. Currently it is completely
  invisible — users see only the final result, not the multi-source pipeline
  that produced it.
- Users cannot discover which tables/columns can be joined across sources
  without manually comparing schemas in their head.
- Performance debugging is blind — a slow cross-source query could be slow
  because of MySQL, HTTP API latency, or DuckDB JOIN. Without per-source
  timing, the bottleneck is invisible.

## Specification

### Orchestration Flow Diagram

After any cross-source query executes, the result view includes a flow diagram:

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ MySQL    │    │ HTTP API │    │ DuckDB   │
│ customers│ →  │ /invoices│ →  │ JOIN     │
│ 7 rows   │    │ 8 rows   │    │ 5 rows   │
│ 156ms    │    │ 250ms    │    │ 62ms     │
└──────────┘    └──────────┘    └──────────┘
```

- Each node = one data source, colored by type (yellow=MySQL, blue=PostgreSQL,
  teal=HTTP API, purple=DuckDB)
- Arrows show data flow direction
- Row counts and per-source timing inside each node
- The slowest node is highlighted with a warning border
- Click a node → show the raw SQL/request that node executed

### Per-Source Timeline

Below the flow diagram, a horizontal waterfall bar chart:

```
MySQL query    ████████████████████████████████  156ms
HTTP API call  ██████████████████████████████████████████████  250ms  ← bottleneck
DuckDB JOIN    ██████████  62ms
```

- Drawn to scale (250ms bar is ~1.6× wider than 156ms bar)
- Bottleneck highlighted in yellow
- Total time shown on the right

### Cross-Source Schema Panel

When multiple data sources are selected in the sidebar, the Schema panel
(right side of Query and Explore modes) shows:

1. **Per-source table tree**: collapsible, with source-type color badge
2. **Cross-source relationship hints**: automatically detected joinable columns

```
─── Schema · 跨源 ─────────────────
┌─ mysql-main ──────────────────┐
│ 📁 customers (7 rows)          │
│   ├ id, name, tax_no, status   │
│   └ metadata (JSON)            │
│ 📁 orders (11 rows)            │
└────────────────────────────────┘
┌─ finance-api ──────────────────┐
│ 🌐 /api/v1/invoices (8 rows)    │
│   ├ invoice_no, seller_name     │
│   ├ buyer_tax_no, total_amount  │
│   └ issue_date                  │
│ 🌐 /api/v1/vouchers (5 rows)    │
└────────────────────────────────┘
┌─ 🔗 关联提示 ──────────────────┐
│ customers.tax_no  ⇄  invoices.buyer_tax_no
│ orders.id         ⇄  vouchers.[order_ref]
└────────────────────────────────┘
```

### Relationship Detection

Cross-source relationship hints are computed server-side:

```python
def detect_relationships(sources: list[Schema]) -> list[Relationship]:
    """Compare column names + types across sources to suggest JOINs."""
    hints = []
    for a, b in itertools.combinations(sources, 2):
        for col_a in a.columns:
            for col_b in b.columns:
                # Match by name similarity + type compatibility
                if names_match(col_a, col_b) and types_compatible(col_a, col_b):
                    hints.append(Relationship(
                        left=f"{a.source_id}.{a.table}.{col_a.name}",
                        right=f"{b.source_id}.{b.table}.{col_b.name}",
                        confidence=compute_confidence(col_a, col_b),
                    ))
    return sorted(hints, key=lambda h: h.confidence, reverse=True)
```

Name matching: exact match, or `_id` suffix match (e.g., `customer_id` ↔ `id`).
Type matching: both INT, or both VARCHAR with similar length.

Low-confidence hints are shown but marked with `⚠` indicator.

### Cross-Source SQL Editor

In Query Mode with multiple sources selected:

- Editor pre-fills DuckDB ATTACH syntax template
- Schema panel click → inserts `source_id.table_name` reference
- Column click → inserts `source_id.table_name.column_name`
- Syntax highlighting for DuckDB-specific keywords (ATTACH, READ_JSON, etc.)

### Integration Points

| Feature | Integrates With |
|---|---|
| Flow diagram appears in Explore mode results | `explore-mode.md` |
| Flow diagram appears in Query mode results | `query-enhancement.md` |
| Flow diagram appears in Monitor detail panel | `agent-monitor.md` |
| Schema hints appear in Schema panel | `console-shell.md` |
| Per-source timing from trace data | `audit-trace.md` |
| DuckDB engine returns per-source metadata | `duckdb-cross-source.md` (needs minor API change) |

### Backend API Change

`duckdb-cross-source.md` engine must return per-source metadata alongside
the result. Add optional field to `StructuredResult`:

```python
class StructuredResult(BaseModel):
    # ... existing fields ...
    source_breakdown: list[SourceBreakdown] | None = None

class SourceBreakdown(BaseModel):
    source_id: str
    source_type: str        # "mysql" | "postgresql" | "http" | "duckdb"
    table: str
    row_count: int
    execution_time_ms: float
```

## Acceptance Criteria

- [ ] Cross-source query with 3 sources → flow diagram shows 3 nodes + arrows
- [ ] Slowest source (HTTP, 250ms) highlighted in the flow diagram
- [ ] Schema panel shows `customers.tax_no ⇄ invoices.buyer_tax_no` hint
- [ ] Click "tax_no" in customers → SQL editor inserts `mysql_main.customers.tax_no`
- [ ] Click relationship hint → SQL editor pre-fills a JOIN template
- [ ] Query with 1 source → no flow diagram shown (not applicable)
- [ ] Flow diagram scales correctly with 2-5 source nodes

## Out of Scope

- Manual relationship editing / overriding auto-detected hints
- Visual query builder (drag-and-drop JOIN)
- Schema diff between source versions
- Data lineage graph across multiple datasets
