# Capability: Dataset Engine (Format-Agnostic Storage)

> **Status**: planned | **Milestone**: M4 | **Owner**: backend | **Depends on**: `duckdb-cross-source`, `mvp-init` M2

## Summary

A format-agnostic dataset storage layer with versioned snapshots. Query results
from any source can be saved as multi-format datasets (JSON / Parquet / Arrow /
CSV), versioned over time, diffed, and exported. DuckDB serves as the query
engine but never as the sole storage format — raw data is always readable by
external tools.

## Motivation

- The current DuckDB engine (`:memory:`) discards all results after each query.
  There is no way to revisit a previous cross-source join, compare "today vs
  yesterday", or share a result with another tool.
- The `_build_insert_values` function converts HTTP JSON results to SQL string
  literals via `str()`, losing type fidelity and nested structure. DuckDB's
  native `read_json()` / `read_parquet()` eliminate this lossy pipeline.
- Storing data exclusively in DuckDB's `.duckdb` format locks users into a
  single tool. Parquet is readable by Pandas, Polars, Spark, Excel, and any
  Arrow-compatible system. JSON is universally readable.
- deepferry's value proposition is "universal data access" — it should not
  become another data silo.

## Specification

### Design Principle

```
Storage  = format-agnostic (JSON / Parquet / Arrow / CSV)
Query    = DuckDB native readers (read_json / read_parquet / read_csv)
Fallback = raw files readable by any external tool
```

### Dataset Directory Structure

```
~/.deepferry/datasets/<dataset_id>/
├── metadata.yaml              ← dataset-level metadata
│   name: "客户发票跨源分析"
│   description: "..."
│   source_query: "JOIN customers ON invoices.buyer_tax_no"
│   sources: [mysql-main, finance-api]
│   versions: [v1, v2, v3]
│   refresh_schedule: weekly
│
├── v1/
│   ├── customers.parquet      ← MySQL snapshot (columnar)
│   ├── invoices.json          ← HTTP API raw response (preserves nesting)
│   ├── joint_result.arrow     ← JOIN result (Arrow IPC, zero-copy)
│   └── manifest.yaml          ← per-version metadata
│       created_at: 2026-06-30T10:00:00Z
│       row_counts: {customers: 7, invoices: 8, joint_result: 5}
│       data_fingerprint: sha256:abc123...
│       refresh_type: full
│
├── v2/
│   └── ...                    ← next snapshot
│
└── _cache/                    ← optional DuckDB cache, can be deleted
    └── query_cache.db
```

### Format Selection by Source Type

| Source Type | Storage Format | Rationale |
|---|---|---|
| MySQL / PostgreSQL | **Parquet** | Columnar, compressed, type-safe, universally readable |
| HTTP API (JSON) | **JSON** (raw) | Preserves nested objects, API response auditability |
| Cross-source JOIN | **Arrow IPC** | Zero-copy between DuckDB/Pandas/Polars, memory-mappable |
| CSV import | **CSV** (raw) + Parquet (index) | Keep original, Parquet for fast queries |
| Large tables (>100K rows) | **Parquet** (partitioned) | Read only needed columns, reduce IO |

### Dataset Manager API

```python
class DatasetManager:
    """CRUD for datasets. All paths relative to data_dir."""

    def __init__(self, data_dir: str):
        self._base = Path(data_dir)

    async def create(self, spec: CreateDatasetSpec) -> Dataset: ...
    async def get(self, dataset_id: str) -> Dataset: ...
    async def list_datasets(self) -> list[DatasetSummary]: ...
    async def delete(self, dataset_id: str) -> None: ...
    async def create_snapshot(
        self, dataset_id: str, result: StructuredResult,
        raw_responses: dict[str, bytes]
    ) -> Snapshot: ...
    async def diff(
        self, dataset_id: str, v1: str, v2: str
    ) -> DiffResult: ...
    async def export(
        self, dataset_id: str, version: str, format: str
    ) -> Path: ...
```

### Snapshot Creation

When a user saves a query result as a dataset:

1. Create `{dataset_id}/v{n}/` directory
2. Write raw HTTP responses as `.json` files (preserve nesting)
3. Write SQL source results as `.parquet` files (via DuckDB `COPY ... TO`)
4. Write JOIN results as `.arrow` files (Arrow IPC format)
5. Write `manifest.yaml` with metadata
6. Append version to `metadata.yaml` version chain
7. Compute SHA256 fingerprint for each file

### DuckDB Engine Upgrade

Replace the current `_build_insert_values` → `INSERT INTO` pipeline
with DuckDB's native readers:

```python
# OLD (lossy, format-locked):
conn.execute("INSERT INTO t VALUES ('escaped_string', 123, ...)")

# NEW (lossless, format-agnostic):
conn.execute("CREATE VIEW invoices AS SELECT * FROM read_json('v1/invoices.json')")
conn.execute("CREATE VIEW customers AS SELECT * FROM read_parquet('v1/customers.parquet')")
conn.execute("SELECT * FROM customers JOIN invoices ON ...")
```

The `_duckdb_cache/` directory is optional — it caches parsed views for
faster subsequent queries but can be deleted and rebuilt from raw files.

### Diff Engine

Uses DuckDB's `EXCEPT` / `INTERSECT` for row-level comparison:

```sql
-- Rows in v2 but not in v1 (additions)
SELECT * FROM v2 EXCEPT SELECT * FROM v1;

-- Rows in v1 but not in v2 (deletions)
SELECT * FROM v1 EXCEPT SELECT * FROM v2;

-- Rows modified (present in both but different)
-- Uses a hash of all columns for efficient comparison
```

### Incremental Refresh

When `refresh_strategy: incremental` and `incremental_column` is set:

```sql
-- MySQL: only fetch rows updated since last snapshot
SELECT * FROM customers WHERE updated_at > '{last_snapshot_ts}';

-- HTTP: pass since parameter
GET /api/v1/invoices?updated_since={last_snapshot_ts}
```

New rows are appended to the existing Parquet/JSON files. Deleted rows
are tracked in a `_tombstones.parquet` file.

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/datasets` | GET | List all datasets |
| `/api/datasets` | POST | Create dataset from query result |
| `/api/datasets/:id` | GET | Dataset detail + version chain |
| `/api/datasets/:id` | DELETE | Delete dataset and all versions |
| `/api/datasets/:id/snapshot` | POST | Create new snapshot (manual refresh) |
| `/api/datasets/:id/diff` | GET | Diff two versions (`?v1&v2`) |
| `/api/datasets/:id/export` | GET | Export version in specified format (`?format=parquet&version=v3`) |

### Storage Estimates

| Scale | Parquet (compressed) | JSON (raw) | Arrow (uncompressed) |
|---|---|---|---|
| 10 cols × 10K rows | ~100 KB | ~1.5 MB | ~800 KB |
| 10 cols × 100K rows | ~2 MB | ~30 MB | ~15 MB |
| 10 cols × 1M rows | ~40 MB | ~300 MB | ~200 MB |

100 datasets × 10 versions at 10K rows each ≈ 100 MB (Parquet) or 1.5 GB (JSON).

## Acceptance Criteria

- [ ] `POST /api/datasets` with explore result → disk has `v1/customers.parquet` + `v1/invoices.json`
- [ ] `GET /api/datasets/:id` → returns metadata with versions list
- [ ] `POST /api/datasets/:id/snapshot` → creates v2 → version chain updated
- [ ] `GET /api/datasets/:id/diff?v1=v1&v2=v2` → returns additions, deletions, modifications
- [ ] `GET /api/datasets/:id/export?format=parquet` → valid Parquet file readable by `pd.read_parquet()`
- [ ] Delete `_cache/` → re-query works (cache rebuilt from raw files)
- [ ] Nested JSON (e.g., `{"tags": ["enterprise"]}`) preserved in `.json` files
- [ ] DuckDB `read_json()` correctly parses nested structures from dataset files

## Out of Scope

- Data lineage / provenance graph (which datasets derived from which sources)
- Automatic schema migration between versions (version compatibility)
- Distributed storage (S3, HDFS) — local filesystem only for M4
- Dataset sharing via URL (security review needed)
