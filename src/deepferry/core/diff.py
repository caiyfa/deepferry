"""Version diff engine using DuckDB EXCEPT/INTERSECT.

Loads two dataset version files into DuckDB temp views (using the appropriate
reader based on file extension), then computes row-level additions and
deletions.  Results are capped at ``_MAX_DIFF_ROWS`` rows each to keep
responses bounded.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.ipc as ipc

from deepferry.core.errors import DataSourceError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import duckdb

_MAX_DIFF_ROWS = 1000


@dataclass
class DiffResult:
    """Row-level differences between two dataset versions."""

    additions: list[dict[str, Any]]
    deletions: list[dict[str, Any]]
    modifications: list[dict[str, Any]]
    summary: dict[str, int] = field(default_factory=dict)


async def diff_versions(
    conn: duckdb.DuckDBPyConnection,
    v1_path: Path,
    v2_path: Path,
) -> DiffResult:
    """Diff two version data files, returning capped addition/deletion lists."""
    return await asyncio.to_thread(_diff_sync, conn, v1_path, v2_path)


def _diff_sync(
    conn: duckdb.DuckDBPyConnection,
    v1_path: Path,
    v2_path: Path,
) -> DiffResult:
    _register_version(conn, "df_v1", v1_path)
    _register_version(conn, "df_v2", v2_path)

    try:
        additions = _fetch_except(conn, "df_v2", "df_v1")
        deletions = _fetch_except(conn, "df_v1", "df_v2")
    except Exception as exc:
        raise DataSourceError(
            code="DIFF_FAILED",
            message=f"Failed to diff {v1_path.name} vs {v2_path.name}: {exc}",
            suggestion="Ensure both versions share compatible schemas.",
        ) from exc

    return DiffResult(
        additions=additions,
        deletions=deletions,
        modifications=[],
        summary={
            "additions": len(additions),
            "deletions": len(deletions),
            "modifications": 0,
        },
    )


def _register_version(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    path: Path,
) -> None:
    suffix = path.suffix.lower()
    quoted = str(path).replace("'", "''")
    if suffix == ".parquet":
        conn.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM read_parquet('{quoted}')")
    elif suffix == ".json":
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM read_json_auto('{quoted}')"
        )
    elif suffix == ".arrow":
        table = _read_arrow(path)
        conn.register(name, table)
    else:
        raise DataSourceError(
            code="UNSUPPORTED_FORMAT",
            message=f"Cannot diff unsupported file format {suffix!r}.",
            suggestion="Use one of: .parquet, .json, .arrow",
        )


def _read_arrow(path: Path) -> pa.Table:
    open_file: Callable[[Any], pa.ipc.RecordBatchFileReader] = ipc.open_file
    try:
        with pa.memory_map(str(path), "r") as source:
            return open_file(source).read_all()
    except (pa.ArrowInvalid, OSError) as exc:
        raise DataSourceError(
            code="DIFF_FAILED",
            message=f"Failed to read Arrow file {path}: {exc}",
            suggestion="Verify the file is a valid Arrow IPC file.",
        ) from exc


def _fetch_except(
    conn: duckdb.DuckDBPyConnection,
    left: str,
    right: str,
) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {left} EXCEPT SELECT * FROM {right}")
    table = cursor.to_arrow_table()
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows[:_MAX_DIFF_ROWS]
