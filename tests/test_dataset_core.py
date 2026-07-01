"""Unit tests for the dataset engine core: storage writers, versioning,
snapshots, fingerprinting, diff, and the DatasetManager orchestration.

Uses in-memory DuckDB connections and ``tmp_path`` isolation — no Docker.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from deepferry.core.dataset import CreateDatasetSpec, DatasetManager
from deepferry.core.dataset_storage import (
    write_arrow_from_result,
    write_csv,
    write_json_from_rows,
    write_parquet,
)
from deepferry.core.diff import diff_versions
from deepferry.core.errors import DataSourceError
from deepferry.core.models import ColumnMeta, StructuredResult
from deepferry.core.snapshot import SnapshotManager
from deepferry.core.versioning import VersionChain

# ── fixtures ────────────────────────────────────────────────────────────


def _cols(*names: str) -> list[ColumnMeta]:
    return [ColumnMeta(name=n, type="any") for n in names]


def _result(rows: list[dict], columns: list[str]) -> StructuredResult:
    return StructuredResult(
        columns=_cols(*columns),
        rows=rows,
        row_count=len(rows),
        execution_time_ms=1.0,
    )


def _customers_result() -> StructuredResult:
    return _result(
        [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        ["id", "name"],
    )


def _invoices_result() -> StructuredResult:
    return _result(
        [{"invoice_id": 10, "buyer": 1, "tags": ["x", "y"]}],
        ["invoice_id", "buyer", "tags"],
    )


def _joint_result() -> StructuredResult:
    return _result(
        [{"customer": "Alice", "invoice": 10}],
        ["customer", "invoice"],
    )


@pytest.fixture
def manager(tmp_path: Path) -> DatasetManager:
    return DatasetManager(str(tmp_path))


@pytest.fixture
def spec() -> CreateDatasetSpec:
    return CreateDatasetSpec(
        name="Customers x Invoices",
        description="cross-source join",
        source_query="SELECT * FROM mysql.customers JOIN http.invoices",
        sources=["mysql-main", "invoices-api"],
        result=_joint_result(),
        source_breakdown={
            "mysql-main": _customers_result(),
            "invoices-api": _invoices_result(),
        },
        refresh_strategy="full",
    )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


# ── DatasetManager CRUD ────────────────────────────────────────────────


async def test_create_dataset_creates_v1_with_metadata(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    dataset = await manager.create(spec)
    assert dataset.versions == ["v1"]
    base = Path(manager._base) / dataset.id
    assert (base / "metadata.json").is_file()
    assert (base / "v1" / "result.arrow").is_file()
    assert (base / "v1" / "manifest.json").is_file()
    assert (base / "v1" / "mysql-main.parquet").is_file()
    assert (base / "v1" / "invoices-api_http.json").is_file()


async def test_get_dataset_returns_full_metadata(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    created = await manager.create(spec)
    fetched = await manager.get(created.id)
    assert fetched.id == created.id
    assert fetched.name == "Customers x Invoices"
    assert fetched.sources == ["mysql-main", "invoices-api"]
    assert fetched.versions == ["v1"]
    assert fetched.refresh_strategy == "full"


async def test_list_datasets_returns_summary(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    await manager.create(spec)
    summaries = await manager.list_datasets()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.name == "Customers x Invoices"
    assert summary.version_count == 1
    assert summary.latest_version == "v1"
    assert summary.total_size_bytes > 0


async def test_delete_dataset_removes_directory(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    created = await manager.create(spec)
    dataset_dir = Path(manager._base) / created.id
    assert dataset_dir.is_dir()
    await manager.delete(created.id)
    assert not dataset_dir.exists()
    with pytest.raises(DataSourceError):
        await manager.get(created.id)


async def test_get_unknown_dataset_raises(manager: DatasetManager) -> None:
    with pytest.raises(DataSourceError):
        await manager.get("does-not-exist")


# ── snapshots & version chain ──────────────────────────────────────────


async def test_create_second_snapshot_creates_v2_and_updates_chain(
    manager: DatasetManager, spec: CreateDatasetSpec
) -> None:
    created = await manager.create(spec)
    spec2 = CreateDatasetSpec(
        name=spec.name,
        description=spec.description,
        source_query=spec.source_query,
        sources=spec.sources,
        result=_result(
            [{"customer": "Alice", "invoice": 10}, {"customer": "Bob", "invoice": 11}],
            ["customer", "invoice"],
        ),
    )
    snapshot = await manager.create_snapshot(created.id, spec2)
    assert snapshot.version == "v2"
    dataset = await manager.get(created.id)
    assert dataset.versions == ["v1", "v2"]


async def test_version_chain_auto_increments(tmp_path: Path) -> None:
    chain = VersionChain(tmp_path / "ds")
    assert chain.next_version() == "v1"
    assert chain.list_versions() == []
    (tmp_path / "ds" / "v1").mkdir(parents=True)
    (tmp_path / "ds" / "v3").mkdir()
    (tmp_path / "ds" / "v2").mkdir()
    assert chain.next_version() == "v4"
    assert chain.list_versions() == ["v1", "v2", "v3"]
    assert chain.latest_version() == "v3"


async def test_version_chain_rejects_invalid_label(tmp_path: Path) -> None:
    chain = VersionChain(tmp_path / "ds")
    with pytest.raises(DataSourceError):
        chain.version_dir("version-1")


# ── fingerprinting ─────────────────────────────────────────────────────


async def test_sha256_fingerprint_stable_for_same_data(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "a.arrow").write_bytes(b"hello")
    (v1 / "b.json").write_bytes(b"world")
    manager = SnapshotManager(tmp_path / "ds")
    fp1 = manager.compute_fingerprint(v1)
    fp2 = manager.compute_fingerprint(v1)
    assert fp1 == fp2
    assert fp1.startswith("sha256:")


async def test_sha256_fingerprint_differs_for_different_data(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "a.arrow").write_bytes(b"hello")
    manager = SnapshotManager(tmp_path / "ds")
    fp1 = manager.compute_fingerprint(v1)
    (v1 / "a.arrow").write_bytes(b"goodbye")
    fp2 = manager.compute_fingerprint(v1)
    assert fp1 != fp2


# ── diff engine ────────────────────────────────────────────────────────


async def test_diff_returns_additions_and_deletions(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    p1 = tmp_path / "v1.parquet"
    p2 = tmp_path / "v2.parquet"
    conn.execute(
        "CREATE TABLE v1 AS SELECT * FROM (VALUES (1,'a'), (2,'b')) AS t(id,name)"
    )
    conn.execute(
        "CREATE TABLE v2 AS SELECT * FROM (VALUES (1,'a'), (3,'c')) AS t(id,name)"
    )
    conn.execute("COPY (SELECT * FROM v1) TO '" + str(p1) + "' (FORMAT PARQUET)")
    conn.execute("COPY (SELECT * FROM v2) TO '" + str(p2) + "' (FORMAT PARQUET)")

    result = await diff_versions(conn, p1, p2)
    assert {tuple(sorted(r.items())) for r in result.additions} == {
        (("id", 3), ("name", "c"))
    }
    assert {tuple(sorted(r.items())) for r in result.deletions} == {
        (("id", 2), ("name", "b"))
    }
    assert result.summary["additions"] == 1
    assert result.summary["deletions"] == 1


async def test_diff_handles_json_source(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    p1 = tmp_path / "v1.json"
    p2 = tmp_path / "v2.json"
    p1.write_text(json.dumps([{"id": 1}, {"id": 2}]))
    p2.write_text(json.dumps([{"id": 2}, {"id": 3}]))
    result = await diff_versions(conn, p1, p2)
    assert result.additions == [{"id": 3}]
    assert result.deletions == [{"id": 1}]


# ── format writers ─────────────────────────────────────────────────────


async def test_export_parquet_readable_by_duckdb(manager: DatasetManager, spec: CreateDatasetSpec, conn: duckdb.DuckDBPyConnection) -> None:
    created = await manager.create(spec)
    exported = await manager.export(created.id, "v1", "parquet")
    rows = conn.execute(f"SELECT * FROM read_parquet('{exported}')").fetchall()
    assert rows == [("Alice", 10)]


async def test_export_csv_has_header(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    created = await manager.create(spec)
    exported = await manager.export(created.id, "v1", "csv")
    text = exported.read_text()
    assert "customer" in text.splitlines()[0]


async def test_export_same_format_returns_existing(manager: DatasetManager, spec: CreateDatasetSpec) -> None:
    created = await manager.create(spec)
    exported = await manager.export(created.id, "v1", "arrow")
    assert exported.name == "result.arrow"


async def test_json_preserves_nested_structure(tmp_path: Path) -> None:
    rows = [{"id": 1, "tags": ["a", "b"], "meta": {"k": "v"}}]
    path = tmp_path / "out.json"
    count = await write_json_from_rows(rows, _cols("id", "tags", "meta"), path)
    assert count == 1
    loaded = json.loads(path.read_text())
    assert loaded[0]["tags"] == ["a", "b"]
    assert loaded[0]["meta"] == {"k": "v"}


async def test_arrow_file_readable_by_pyarrow(tmp_path: Path) -> None:
    result = _result(
        [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        ["id", "name"],
    )
    path = tmp_path / "out.arrow"
    count = await write_arrow_from_result(result, path)
    assert count == 2
    with pa.memory_map(str(path), "r") as source:
        table = ipc.open_file(source).read_all()
    assert table.to_pylist() == result.rows


async def test_parquet_writer_via_duckdb(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    conn.execute("CREATE TABLE src AS SELECT range AS n FROM range(3)")
    path = tmp_path / "out.parquet"
    count = await write_parquet(conn, "SELECT n FROM src", path)
    assert count == 3
    assert path.is_file()
    rows = conn.execute(f"SELECT * FROM read_parquet('{path}') ORDER BY n").fetchall()
    assert rows == [(0,), (1,), (2,)]


async def test_csv_writer_via_duckdb(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    conn.execute("CREATE TABLE src AS SELECT 'x' AS letter")
    path = tmp_path / "out.csv"
    count = await write_csv(conn, "SELECT letter FROM src", path)
    assert count == 1
    assert "letter" in path.read_text()


# ── snapshot manager ──────────────────────────────────────────────────


async def test_snapshot_manifest_roundtrip(tmp_path: Path) -> None:
    ds_dir = tmp_path / "ds"
    ds_dir.mkdir()
    manager = SnapshotManager(ds_dir)
    snapshot = await manager.create_snapshot(result=_joint_result())
    assert snapshot.version == "v1"
    manifest = manager.read_manifest("v1")
    assert manifest["version"] == "v1"
    assert manifest["data_fingerprint"].startswith("sha256:")
    assert manifest["row_counts"]["joint_result"] == 1


async def test_snapshot_read_missing_manifest_raises(tmp_path: Path) -> None:
    ds_dir = tmp_path / "ds"
    ds_dir.mkdir()
    manager = SnapshotManager(ds_dir)
    with pytest.raises(DataSourceError):
        manager.read_manifest("v1")
