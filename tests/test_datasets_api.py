"""Tests for the Dataset REST API (``/datasets`` routes).

Uses httpx with an ASGITransport against the real FastAPI app, backed by a
``DatasetManager`` rooted in ``tmp_path`` — no Docker, no network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import app, init_app


def _base_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "test-dataset",
        "description": "test",
        "source_query": "SELECT 1",
        "sources": ["test-source"],
        "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
        "rows": [{"id": 1}, {"id": 2}],
    }
    body.update(overrides)
    return body


def _second_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "test-dataset",
        "description": "v2",
        "source_query": "SELECT 1",
        "sources": ["test-source"],
        "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
        "rows": [{"id": 1}, {"id": 2}, {"id": 3}],
    }
    body.update(overrides)
    return body


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture
def _setup(tmp_path):
    init_app(SourceRegistry(), data_dir=str(tmp_path))


async def test_list_empty_returns_empty_array(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get("/datasets")
    assert resp.status_code == 200
    assert resp.json() == {"datasets": []}


async def test_create_dataset_returns_201_with_id(_setup) -> None:
    async with _make_client() as client:
        resp = await client.post("/datasets", json=_base_body())
    assert resp.status_code == 201
    dataset = resp.json()["dataset"]
    assert dataset["id"]
    assert dataset["name"] == "test-dataset"
    assert dataset["versions"] == ["v1"]


async def test_create_then_list_returns_one_dataset(_setup) -> None:
    async with _make_client() as client:
        await client.post("/datasets", json=_base_body())
        resp = await client.get("/datasets")
    assert resp.status_code == 200
    datasets = resp.json()["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["name"] == "test-dataset"
    assert datasets[0]["version_count"] == 1
    assert datasets[0]["latest_version"] == "v1"


async def test_create_then_get_returns_full_metadata(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.get(f"/datasets/{created['id']}")
    assert resp.status_code == 200
    dataset = resp.json()["dataset"]
    assert dataset["id"] == created["id"]
    assert dataset["sources"] == ["test-source"]
    assert dataset["source_query"] == "SELECT 1"
    assert dataset["refresh_strategy"] == "full"


async def test_get_unknown_returns_404(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get("/datasets/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DATASET_NOT_FOUND"


async def test_delete_dataset_returns_success(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.delete(f"/datasets/{created['id']}")
        after = await client.get("/datasets")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert after.json() == {"datasets": []}


async def test_delete_unknown_returns_404(_setup) -> None:
    async with _make_client() as client:
        resp = await client.delete("/datasets/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DATASET_NOT_FOUND"


async def test_create_snapshot_creates_v2(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.post(
            f"/datasets/{created['id']}/snapshot", json=_second_body()
        )
        detail = await client.get(f"/datasets/{created['id']}")
    assert resp.status_code == 201
    snapshot = resp.json()["snapshot"]
    assert snapshot["version"] == "v2"
    assert snapshot["row_counts"]["joint_result"] == 3
    dataset = detail.json()["dataset"]
    assert dataset["versions"] == ["v1", "v2"]


async def test_diff_returns_additions_and_deletions(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        v2_body = _second_body(rows=[{"id": 2}, {"id": 3}])
        await client.post(f"/datasets/{created['id']}/snapshot", json=v2_body)
        resp = await client.get(
            f"/datasets/{created['id']}/diff", params={"v1": "v1", "v2": "v2"}
        )
    assert resp.status_code == 200
    diff = resp.json()["diff"]
    assert diff["summary"]["additions"] == 1
    assert diff["summary"]["deletions"] == 1
    assert any(r.get("id") == 3 for r in diff["additions"])
    assert any(r.get("id") == 1 for r in diff["deletions"])


async def test_diff_unknown_dataset_returns_404(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get(
            "/datasets/missing/diff", params={"v1": "v1", "v2": "v2"}
        )
    assert resp.status_code == 404


async def test_export_parquet_downloads_file(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.get(
            f"/datasets/{created['id']}/export",
            params={"format": "parquet", "version": "v1"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith("_v1.parquet\"")
    assert len(resp.content) > 0


async def test_export_csv_downloads_file(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.get(
            f"/datasets/{created['id']}/export",
            params={"format": "csv", "version": "v1"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"].endswith("_v1.csv\"")
    text = resp.text
    assert "id" in text.splitlines()[0]


async def test_export_json_downloads_file(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.get(
            f"/datasets/{created['id']}/export",
            params={"format": "json", "version": "v1"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["content-disposition"].endswith("_v1.json\"")
    assert b"id" in resp.content


async def test_export_unknown_dataset_returns_404(_setup) -> None:
    async with _make_client() as client:
        resp = await client.get(
            "/datasets/missing/export",
            params={"format": "parquet", "version": "v1"},
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DATASET_NOT_FOUND"


async def test_export_bad_format_returns_400(_setup) -> None:
    async with _make_client() as client:
        created = (await client.post("/datasets", json=_base_body())).json()["dataset"]
        resp = await client.get(
            f"/datasets/{created['id']}/export",
            params={"format": "xml", "version": "v1"},
        )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_FORMAT"


async def test_snapshot_unknown_dataset_returns_404(_setup) -> None:
    async with _make_client() as client:
        resp = await client.post(
            "/datasets/missing/snapshot", json=_second_body()
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DATASET_NOT_FOUND"
