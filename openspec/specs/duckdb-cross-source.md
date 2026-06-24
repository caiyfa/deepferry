# Capability: DuckDB Cross-Source JOIN

> **Status**: planned | **Milestone**: M4 | **Owner**: backend

## Summary

Enable cross-source SQL JOINs using DuckDB as an in-process OLAP engine. Materialize HTTP API JSON responses as DuckDB views, then JOIN them with MySQL/PostgreSQL tables via DuckDB's `mysql_scanner` extension.

## Motivation

- Agents often need to answer questions that span multiple data sources (e.g., "top customers by order value" where customers are in MySQL and orders come from an HTTP API)
- DuckDB's built-in cross-database query capabilities make this feasible without a separate data warehouse
- This is the "wow factor" demo capability for the 30-second showcase video

## Specification

### Architecture

```
Agent: "Find top 10 customers by total order value"

      ┌──────────────────────────────────────┐
      │            DuckDB Engine              │
      │                                      │
      │  ┌─────────────┐  ┌──────────────┐   │
      │  │ MySQL Table  │  │ HTTP API     │   │
      │  │  (customers) │  │  (orders)    │   │
      │  │              │  │              │   │
      │  │ ATTACH       │  │ JSON →       │   │
      │  │ 'mysql:...'  │  │ Schema Infer │   │
      │  │              │  │ → View       │   │
      │  └──────┬───────┘  └──────┬───────┘   │
      │         │                 │           │
      │         └────────┬────────┘           │
      │                  ▼                    │
      │         SELECT c.name, SUM(o.amount)  │
      │         FROM customers c              │
      │         JOIN orders_view o            │
      │           ON c.id = o.user_id         │
      │         GROUP BY c.name               │
      │         ORDER BY 2 DESC LIMIT 10      │
      └──────────────────────────────────────┘
```

### JSON Schema Inference

HTTP API JSON response → DuckDB schema → CREATE VIEW:

```python
class SchemaInferrer:
    def infer(self, sample_rows: list[dict], table_name: str) -> str:
        """
        Infer DuckDB CREATE TABLE statement from sample JSON rows.
        
        Strategy:
        1. Sample N rows (configurable, default 100)
        2. For each key, detect type across samples
        3. Coalesce: int > float > varchar (widest wins)
        4. NULLs: treat as VARCHAR fallback
        5. Nested objects: flatten to parent_child VARCHAR
        6. Arrays: store as VARCHAR (JSON string) or LIST type
        """
        ...
```

### Materialization Strategy

| Strategy | When | Trade-off |
|----------|------|-----------|
| **Full materialization** | API response <10K rows | Simple, fast queries |
| **Incremental refresh** | API supports pagination/date filters | Stale data acceptable |
| **On-demand** | Every query re-fetches API | Always fresh, slower |

MVP implements **full materialization** (simplest, sufficient for demo).

```python
async def materialize_http_source(source: HttpApiDataSource) -> str:
    """Materialize HTTP API data into DuckDB view."""
    result = await source.execute_all_endpoints()  # Fetch all configured endpoints
    for endpoint_name, rows in result.items():
        schema_sql = inferrer.infer(rows, endpoint_name)
        duckdb.execute(f"CREATE OR REPLACE TABLE {endpoint_name} AS SELECT * FROM read_json_auto('{json_path}')")
    return endpoint_name
```

### DuckDB MySQL Scanner

DuckDB's built-in `mysql_scanner` extension enables direct MySQL queries:

```sql
INSTALL mysql;
LOAD mysql;
ATTACH 'host=localhost user=root database=mydb' AS mysql_db (TYPE mysql);
SELECT * FROM mysql_db.customers LIMIT 10;
```

For PostgreSQL, use `postgres_scanner`:

```sql
INSTALL postgres;
LOAD postgres;
ATTACH 'host=localhost dbname=mydb' AS pg_db (TYPE postgres);
```

### Demo Scenario (E-Commerce Analysis)

```
Data Source A (MySQL): customers table
  └── id, name, email, created_at

Data Source B (HTTP API, two-step auth): /orders endpoint
  └── user_id, amount, created_at, status

Agent prompt: "Find the top 10 customers by total spending"

Execution:
1. Two-step auth login to orders API
2. Fetch order JSON, infer schema, materialize as DuckDB view
3. DuckDB JOIN: customers ⋈ orders ON id = user_id
4. GROUP BY + SUM(amount) + ORDER BY + LIMIT 10
5. Display results in desktop app
```

## Acceptance Criteria (M4)

1. Demo scenario runs end-to-end from agent query to displayed results
2. DuckDB correctly infers schema from JSON response (≥90% accuracy for flat structures)
3. Cross-source JOIN returns correct results (manual verification against expected)
4. Materialized view refreshes correctly on re-query
5. Degradation path: if DuckDB materialization fails, fall back to Python-side join with clear warning

### Degradation Plan

If DuckDB JSON materialization proves too complex for M4 timeline:

> **Fallback**: Python-level JOIN using pandas — fetch both datasets, join in memory, return result. DuckDB materialization becomes a v2 enhancement. This still proves the concept but with worse performance for large datasets.

## Interview Story

"The cross-source JOIN is the culmination of all previous milestones. DuckDB was chosen over Presto/Trino because it's embedded (no server to manage), has built-in MySQL/PostgreSQL scanners, and the C++ engine is fast enough for MVP-scale data. The JSON schema inference is the real engineering challenge — sampling types across rows and choosing the right coalescence strategy."

## Dependencies

- [[sql-datasource]] — MySQL/PostgreSQL data sources
- [[http-api-datasource]] — HTTP API data source (materialization target)
- [[two-step-auth]] — Auth for protected HTTP APIs
