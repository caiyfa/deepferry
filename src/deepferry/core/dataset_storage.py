"""Format-agnostic dataset writers — Parquet, JSON, Arrow IPC, CSV.

Each writer returns the row count written.  Blocking file/DB IO is wrapped
with :func:`asyncio.to_thread` so callers can ``await`` from async contexts
without blocking the event loop.  Parquet and CSV use DuckDB ``COPY``;
JSON uses :func:`json.dumps` to preserve nested objects and arrays; Arrow uses
``pyarrow.ipc`` for zero-copy columnar storage.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.ipc as ipc

from deepferry.core.errors import DataSourceError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Any

    import duckdb

    from deepferry.core.models import ColumnMeta, StructuredResult


async def write_parquet(conn: duckdb.DuckDBPyConnection, table_or_query: str, path: Path) -> int:
    """Write ``table_or_query`` to ``path`` as Parquet via DuckDB COPY."""
    count = await asyncio.to_thread(_copy_to, conn, table_or_query, path, "PARQUET")
    return count


async def write_csv(conn: duckdb.DuckDBPyConnection, table_or_query: str, path: Path) -> int:
    """Write ``table_or_query`` to ``path`` as CSV (with header) via DuckDB COPY."""
    count = await asyncio.to_thread(_copy_to, conn, table_or_query, path, "CSV")
    return count


async def write_json_from_rows(
    rows: list[dict[str, Any]], columns: list[ColumnMeta], path: Path
) -> int:
    """Write ``rows`` as a JSON array, preserving nested lists and dicts."""
    count = await asyncio.to_thread(_write_json_sync, rows, path)
    _ = columns
    return count


async def write_arrow_from_result(result: StructuredResult, path: Path) -> int:
    """Write ``result`` as an Arrow IPC file via pyarrow."""
    count = await asyncio.to_thread(_write_arrow_sync, result.rows, path)
    return count


# ── sync workers ────────────────────────────────────────────────────────


def _copy_to(
    conn: duckdb.DuckDBPyConnection,
    table_or_query: str,
    path: Path,
    fmt: str,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted = str(path).replace("'", "''")
    options = "(FORMAT CSV, HEADER true)" if fmt == "CSV" else f"(FORMAT {fmt})"
    count_sql = f"SELECT COUNT(*) FROM ({table_or_query}) AS _src"
    try:
        row_count: int = conn.execute(count_sql).fetchone()[0]  # type: ignore[index]
        conn.execute(f"COPY ({table_or_query}) TO '{quoted}' {options}")
    except Exception as exc:
        raise DataSourceError(
            code="DATASET_WRITE_FAILED",
            message=f"Failed to write {fmt} to {path}: {exc}",
            suggestion="Check the query/table name and DuckDB connection state.",
        ) from exc
    return row_count


def _write_json_sync(rows: list[dict[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise DataSourceError(
            code="DATASET_WRITE_FAILED",
            message=f"Failed to write JSON to {path}: {exc}",
            suggestion="Verify the target directory is writable.",
        ) from exc
    return len(rows)


def _write_arrow_sync(rows: list[dict[str, Any]], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file: Callable[..., pa.ipc.RecordBatchFileWriter] = ipc.new_file
    try:
        table = pa.Table.from_pylist(rows)
        with pa.OSFile(str(path), "wb") as sink, new_file(sink, table.schema) as writer:
            writer.write_table(table)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OSError) as exc:
        raise DataSourceError(
            code="DATASET_WRITE_FAILED",
            message=f"Failed to write Arrow IPC to {path}: {exc}",
            suggestion="Ensure rows contain consistent types across each column.",
        ) from exc
    return len(rows)
