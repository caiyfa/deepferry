"""Dataset CRUD and orchestration — the top-level entry point.

A dataset is a directory ``{base}/datasets/{id}/`` holding ``metadata.json``
plus one subdirectory per version (``v1/``, ``v2/``, ...).  The
:class:`DatasetManager` creates, lists, reads, and deletes datasets; creates
new snapshots via :class:`SnapshotManager`; diffs two versions via the DuckDB
diff engine; and exports a version into any supported format.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepferry.core.dataset_storage import (
    write_arrow_from_result,
    write_csv,
    write_json_from_rows,
    write_parquet,
)
from deepferry.core.diff import DiffResult, diff_versions
from deepferry.core.errors import DataSourceError
from deepferry.core.models import ColumnMeta, StructuredResult
from deepferry.core.snapshot import Snapshot, SnapshotManager

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class CreateDatasetSpec:
    """Inputs needed to create a dataset or a new snapshot."""

    name: str
    description: str
    source_query: str
    sources: list[str]
    result: StructuredResult
    source_breakdown: dict[str, StructuredResult] | None = None
    refresh_strategy: str = "full"
    incremental_column: str | None = None


@dataclass
class Dataset:
    """Persisted dataset-level metadata."""

    id: str
    name: str
    description: str
    source_query: str
    sources: list[str]
    versions: list[str]
    created_at: str
    refresh_strategy: str
    incremental_column: str | None


@dataclass
class DatasetSummary:
    """Lightweight summary used when listing datasets."""

    id: str
    name: str
    version_count: int
    latest_version: str | None
    total_size_bytes: int


class DatasetManager:
    """CRUD + snapshot/diff/export orchestration for datasets."""

    def __init__(self, data_dir: str) -> None:
        self._base = Path(data_dir).expanduser() / "datasets"
        self._base.mkdir(parents=True, exist_ok=True)

    async def create(self, spec: CreateDatasetSpec) -> Dataset:
        dataset_id = uuid.uuid4().hex
        dataset_dir = self._base / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        created_at = _now_iso()
        conn = await asyncio.to_thread(_new_connection)
        try:
            snapshot = await SnapshotManager(dataset_dir).create_snapshot(
                result=spec.result,
                source_breakdown=spec.source_breakdown,
                conn=conn,
                refresh_type=spec.refresh_strategy,
            )
        finally:
            await asyncio.to_thread(conn.close)
        dataset = Dataset(
            id=dataset_id,
            name=spec.name,
            description=spec.description,
            source_query=spec.source_query,
            sources=list(spec.sources),
            versions=[snapshot.version],
            created_at=created_at,
            refresh_strategy=spec.refresh_strategy,
            incremental_column=spec.incremental_column,
        )
        await self._write_metadata(dataset_dir, dataset)
        return dataset

    async def get(self, dataset_id: str) -> Dataset:
        dataset_dir = self._require_dataset(dataset_id)
        return await asyncio.to_thread(self._read_metadata, dataset_dir)

    async def list_datasets(self) -> list[DatasetSummary]:
        summaries: list[DatasetSummary] = []
        for child in self._base.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "metadata.json"
            if not meta_path.is_file():
                continue
            dataset = await asyncio.to_thread(self._read_metadata, child)
            total_size = await asyncio.to_thread(_dir_size, child)
            summaries.append(
                DatasetSummary(
                    id=dataset.id,
                    name=dataset.name,
                    version_count=len(dataset.versions),
                    latest_version=dataset.versions[-1] if dataset.versions else None,
                    total_size_bytes=total_size,
                )
            )
        summaries.sort(key=lambda s: s.name)
        return summaries

    async def delete(self, dataset_id: str) -> None:
        dataset_dir = self._require_dataset(dataset_id)
        await asyncio.to_thread(_rmtree, dataset_dir)

    async def create_snapshot(self, dataset_id: str, spec: CreateDatasetSpec) -> Snapshot:
        dataset_dir = self._require_dataset(dataset_id)
        conn = await asyncio.to_thread(_new_connection)
        try:
            snapshot = await SnapshotManager(dataset_dir).create_snapshot(
                result=spec.result,
                source_breakdown=spec.source_breakdown,
                conn=conn,
                refresh_type=spec.refresh_strategy,
            )
        finally:
            await asyncio.to_thread(conn.close)
        dataset = await asyncio.to_thread(self._read_metadata, dataset_dir)
        if snapshot.version not in dataset.versions:
            dataset.versions.append(snapshot.version)
        await self._write_metadata(dataset_dir, dataset)
        return snapshot

    async def diff(self, dataset_id: str, v1: str, v2: str) -> DiffResult:
        dataset_dir = self._require_dataset(dataset_id)
        conn = await asyncio.to_thread(_new_connection)
        try:
            f1 = await asyncio.to_thread(_find_main_result, dataset_dir / v1)
            f2 = await asyncio.to_thread(_find_main_result, dataset_dir / v2)
            if f1 is None or f2 is None:
                raise DataSourceError(
                    code="SNAPSHOT_NOT_FOUND",
                    message=f"Cannot diff {v1}/{v2}: a version directory is missing a result file.",
                    suggestion="Ensure both versions were created successfully.",
                )
            return await diff_versions(conn, f1, f2)
        finally:
            await asyncio.to_thread(conn.close)

    async def export(self, dataset_id: str, version: str, format: str) -> Path:
        dataset_dir = self._require_dataset(dataset_id)
        version_dir = dataset_dir / version
        if not version_dir.is_dir():
            raise DataSourceError(
                code="VERSION_NOT_FOUND",
                message=f"Version {version!r} does not exist in dataset {dataset_id!r}.",
                suggestion="Check available versions via get().",
            )
        source_file = await asyncio.to_thread(_find_main_result, version_dir)
        if source_file is None:
            raise DataSourceError(
                code="SNAPSHOT_NOT_FOUND",
                message=f"No result file found in version {version!r}.",
                suggestion="Re-create the snapshot.",
            )
        fmt = format.lower().lstrip(".")
        target_suffix = f".{fmt}"
        if source_file.suffix.lower() == target_suffix:
            return source_file
        target = version_dir / f"export{target_suffix}"
        return await self._convert(source_file=source_file, target=target, fmt=fmt)

    async def _convert(self, source_file: Path, target: Path, fmt: str) -> Path:
        live = await asyncio.to_thread(_new_connection)
        try:
            cols, rows = await asyncio.to_thread(_load_rows, live, source_file)
            column_meta = [ColumnMeta(name=c, type="any") for c in cols]
            if fmt in ("parquet", "csv"):
                table = _rows_to_table(rows, cols)
                live.register("_export", table)
                if fmt == "parquet":
                    await write_parquet(live, "SELECT * FROM _export", target)
                else:
                    await write_csv(live, "SELECT * FROM _export", target)
            elif fmt == "json":
                await write_json_from_rows(rows, column_meta, target)
            elif fmt == "arrow":
                await write_arrow_from_result(
                    StructuredResult(
                        columns=column_meta,
                        rows=rows,
                        row_count=len(rows),
                        execution_time_ms=0.0,
                    ),
                    target,
                )
            else:
                raise DataSourceError(
                    code="UNSUPPORTED_FORMAT",
                    message=f"Cannot export to unsupported format {fmt!r}.",
                    suggestion="Use one of: parquet, csv, json, arrow.",
                )
        finally:
            await asyncio.to_thread(live.close)
        return target

    # ── metadata helpers ──────────────────────────────────────────────────

    def _require_dataset(self, dataset_id: str) -> Path:
        dataset_dir = self._base / dataset_id
        if not dataset_dir.is_dir():
            raise DataSourceError(
                code="DATASET_NOT_FOUND",
                message=f"Dataset {dataset_id!r} does not exist.",
                suggestion="List datasets with list_datasets() first.",
            )
        return dataset_dir

    def _read_metadata(self, dataset_dir: Path) -> Dataset:
        meta_path = dataset_dir / "metadata.json"
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataSourceError(
                code="DATASET_CORRUPT",
                message=f"Metadata for dataset at {dataset_dir.name} is unreadable: {exc}",
                suggestion="Recreate the dataset if it cannot be recovered.",
            ) from exc
        return Dataset(
            id=payload["id"],
            name=payload["name"],
            description=payload.get("description", ""),
            source_query=payload.get("source_query", ""),
            sources=list(payload.get("sources", [])),
            versions=list(payload.get("versions", [])),
            created_at=payload.get("created_at", _now_iso()),
            refresh_strategy=payload.get("refresh_strategy", "full"),
            incremental_column=payload.get("incremental_column"),
        )

    async def _write_metadata(self, dataset_dir: Path, dataset: Dataset) -> None:
        payload = asdict(dataset)
        await asyncio.to_thread(
            _write_json,
            dataset_dir / "metadata.json",
            payload,
        )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _new_connection() -> Any:
    import duckdb

    return duckdb.connect(":memory:")


def _read_sql(source_file: Path) -> str:
    suffix = source_file.suffix.lower()
    quoted = str(source_file).replace("'", "''")
    if suffix == ".parquet":
        return f"SELECT * FROM read_parquet('{quoted}')"
    if suffix == ".json":
        return f"SELECT * FROM read_json_auto('{quoted}')"
    raise DataSourceError(
        code="UNSUPPORTED_FORMAT",
        message=f"Unsupported source format {suffix!r}.",
        suggestion="Use one of: .parquet, .json, .arrow",
    )


def _rows_to_table(rows: list[dict[str, Any]], cols: list[str]) -> Any:
    import pyarrow as pa

    if rows:
        return pa.Table.from_pylist(rows)
    fields = [pa.field(c, pa.null()) for c in cols]
    return pa.table({c: pa.array([], type=pa.null()) for c in cols}, schema=pa.schema(fields))


def _load_rows(conn: Any, source_file: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if source_file.suffix.lower() == ".arrow":
        import pyarrow as pa
        import pyarrow.ipc as ipc

        open_file: Callable[[Any], pa.ipc.RecordBatchFileReader] = ipc.open_file
        with pa.memory_map(str(source_file), "r") as source:
            table = open_file(source).read_all()
        cols = list(table.schema.names)
        rows: list[dict[str, Any]] = table.to_pylist()
        return cols, rows
    sql = _read_sql(source_file)
    cursor = conn.execute(sql)
    cols = [d[0] for d in cursor.description]
    table = cursor.to_arrow_table()
    loaded: list[dict[str, Any]] = table.to_pylist()
    return cols, loaded


def _find_main_result(version_dir: Path) -> Path | None:
    if not version_dir.is_dir():
        return None
    arrow = version_dir / "result.arrow"
    if arrow.is_file():
        return arrow
    for candidate in sorted(version_dir.iterdir()):
        if candidate.suffix.lower() in {".parquet", ".json"}:
            return candidate
    return None
