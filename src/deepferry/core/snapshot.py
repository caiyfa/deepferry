"""Versioned snapshot creation for datasets.

A snapshot freezes a query result (and optional per-source breakdown) into a
single version directory alongside a ``manifest.json`` recording row counts,
a combined SHA256 fingerprint, and the file inventory.  Source results are
written in their canonical format: SQL sources → Parquet (via DuckDB COPY),
HTTP sources → JSON (preserving nesting); the main/join result → Arrow IPC.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pyarrow as pa

from deepferry.core.dataset_storage import (
    write_arrow_from_result,
    write_json_from_rows,
    write_parquet,
)
from deepferry.core.errors import DataSourceError
from deepferry.core.versioning import VersionChain

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    import duckdb

    from deepferry.core.models import StructuredResult

_MAIN_RESULT_FILE = "result.arrow"

_HTTP_SOURCE_RE = re.compile(r"(http|api|rest|orchestrat|web)", re.IGNORECASE)


@dataclass
class Snapshot:
    """Metadata describing a single versioned snapshot."""

    version: str
    created_at: str
    row_counts: dict[str, int]
    data_fingerprint: str
    refresh_type: str
    files: list[str] = field(default_factory=list)


class SnapshotManager:
    """Creates and reads versioned snapshots within a dataset directory."""

    def __init__(self, dataset_dir: Path) -> None:
        self._dir = dataset_dir
        self._chain = VersionChain(dataset_dir)

    async def create_snapshot(
        self,
        result: StructuredResult,
        source_breakdown: dict[str, StructuredResult] | None = None,
        conn: duckdb.DuckDBPyConnection | None = None,
        refresh_type: str = "full",
    ) -> Snapshot:
        version = self._chain.next_version()
        version_dir = self._chain.version_dir(version)
        version_dir.mkdir(parents=True, exist_ok=True)

        row_counts: dict[str, int] = {}
        files: list[str] = []

        if source_breakdown:
            for source_id, src_result in source_breakdown.items():
                fname = f"{source_id}{'_http.json' if _is_http_source(source_id) else '.parquet'}"
                fpath = version_dir / fname
                if _is_http_source(source_id):
                    await write_json_from_rows(src_result.rows, src_result.columns, fpath)
                else:
                    if conn is None:
                        raise DataSourceError(
                            code="DATASET_WRITE_FAILED",
                            message=f"Cannot snapshot SQL source {source_id!r} as Parquet: no DuckDB connection provided.",
                            suggestion="Pass a live DuckDB connection to create_snapshot().",
                        )
                    safe_source = re.sub(r"\W", "_", source_id)
                    view_name = f"_snap_{safe_source}_{version}"
                    conn.register(view_name, pa.Table.from_pylist(src_result.rows))
                    await write_parquet(conn, f"SELECT * FROM {view_name}", fpath)
                    conn.unregister(view_name)
                row_counts[source_id] = src_result.row_count
                files.append(fname)

        await write_arrow_from_result(result, version_dir / _MAIN_RESULT_FILE)
        row_counts["joint_result"] = result.row_count
        files.append(_MAIN_RESULT_FILE)

        fingerprint = await asyncio.to_thread(self.compute_fingerprint, version_dir)
        snapshot = Snapshot(
            version=version,
            created_at=datetime.now(UTC).isoformat(),
            row_counts=row_counts,
            data_fingerprint=fingerprint,
            refresh_type=refresh_type,
            files=sorted(files),
        )
        await asyncio.to_thread(_write_manifest, version_dir, snapshot)
        return snapshot

    def compute_fingerprint(self, version_dir: Path) -> str:
        hasher = hashlib.sha256()
        data_files = sorted(
            p for p in version_dir.iterdir() if p.is_file() and p.name != "manifest.json"
        )
        for fpath in data_files:
            hasher.update(fpath.read_bytes())
        return f"sha256:{hasher.hexdigest()}"

    def read_manifest(self, version: str) -> dict[str, Any]:
        manifest = self._chain.version_dir(version) / "manifest.json"
        if not manifest.is_file():
            raise DataSourceError(
                code="SNAPSHOT_NOT_FOUND",
                message=f"No manifest found for version {version!r}.",
                suggestion="Check list_versions() for valid version labels.",
            )
        try:
            payload: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
            return payload
        except (json.JSONDecodeError, OSError) as exc:
            raise DataSourceError(
                code="SNAPSHOT_CORRUPT",
                message=f"Manifest for {version!r} is unreadable: {exc}",
                suggestion="Re-create the snapshot if it cannot be recovered.",
            ) from exc


def _is_http_source(source_id: str) -> bool:
    return bool(_HTTP_SOURCE_RE.search(source_id))


def _write_manifest(version_dir: Path, snapshot: Snapshot) -> None:
    payload: dict[str, Any] = {
        "version": snapshot.version,
        "created_at": snapshot.created_at,
        "row_counts": snapshot.row_counts,
        "data_fingerprint": snapshot.data_fingerprint,
        "refresh_type": snapshot.refresh_type,
        "files": snapshot.files,
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
