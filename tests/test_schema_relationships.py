"""Tests for GET /schema/relationships — cross-source JOIN candidate detection."""

from __future__ import annotations

import httpx

from deepferry.core.models import ColumnMeta, ResourceMeta, Schema
from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import app, init_app

# ── Fake source ─────────────────────────────────────────────────────────


class FakeSource:
    """Minimal duck-type source exposing only ``schema_info``.

    Registered directly into the registry ``_instances`` dict so we can run
    the real endpoint without Docker or network dependencies.
    """

    source_type: str = "fake"

    def __init__(self, source_id: str, schema: Schema) -> None:
        self.source_id = source_id
        self._schema = schema

    async def schema_info(self, resource: str | None = None) -> Schema:  # noqa: ARG002
        return self._schema


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _mk_registry(sources: dict[str, FakeSource]) -> SourceRegistry:
    """Build a SourceRegistry and inject *sources* into its ``_instances``."""
    registry = SourceRegistry()
    # Populate an _instances-compatible dict (id → object with .source_id and .schema_info)
    registry._instances = sources  # type: ignore[assignment]
    return registry


def _mk_schema(resources: list[ResourceMeta]) -> Schema:
    return Schema(resources=resources)


def _col(name: str, col_type: str = "VARCHAR", nullable: bool = True) -> ColumnMeta:
    return ColumnMeta(name=name, type=col_type, nullable=nullable)


def _res(name: str, *columns: ColumnMeta) -> ResourceMeta:
    return ResourceMeta(name=name, columns=list(columns))


# ── Tests ───────────────────────────────────────────────────────────────


class TestGetRelationships:
    """Integration-style tests for GET /schema/relationships."""

    # ── 1. Two sources sharing user_id (INT) → high-confidence match ──

    async def test_shared_column_returns_high_confidence(self) -> None:
        src_a = FakeSource(
            "mysql",
            _mk_schema([_res("users", _col("user_id", "INT"), _col("name", "VARCHAR"))]),
        )
        src_b = FakeSource(
            "pg",
            _mk_schema([_res("profiles", _col("user_id", "INT"), _col("bio", "TEXT"))]),
        )
        registry = _mk_registry({"mysql": src_a, "pg": src_b})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/relationships")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        r = data[0]
        assert r["confidence"] >= 0.8  # 0.5 base + 0.3 type + 0.2 id-suffix
        assert r["left"]["column"] == "user_id" or r["right"]["column"] == "user_id"

    # ── 2. No common columns → empty list ──

    async def test_no_common_columns_returns_empty(self) -> None:
        src_a = FakeSource(
            "mysql",
            _mk_schema([_res("t1", _col("alpha", "INT"), _col("beta", "VARCHAR"))]),
        )
        src_b = FakeSource(
            "pg",
            _mk_schema([_res("t2", _col("gamma", "INT"), _col("delta", "VARCHAR"))]),
        )
        registry = _mk_registry({"mysql": src_a, "pg": src_b})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/relationships")

        assert resp.status_code == 200
        assert resp.json() == []

    # ── 3. Single source (no cross-source possible) → empty list ──

    async def test_single_source_returns_empty(self) -> None:
        src = FakeSource(
            "mysql",
            _mk_schema([_res("t1", _col("id", "INT"), _col("name", "VARCHAR"))]),
        )
        registry = _mk_registry({"mysql": src})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/relationships")

        assert resp.status_code == 200
        assert resp.json() == []

    # ── 4. Compatible types (VARCHAR vs TEXT) → +0.3 type bonus ──

    async def test_compatible_types_earn_type_bonus(self) -> None:
        src_a = FakeSource(
            "mysql",
            _mk_schema([_res("t1", _col("username", "VARCHAR"))]),
        )
        src_b = FakeSource(
            "pg",
            _mk_schema([_res("t2", _col("username", "TEXT"))]),
        )
        registry = _mk_registry({"mysql": src_a, "pg": src_b})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/relationships")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        r = data[0]
        assert r["confidence"] >= 0.8  # 0.5 + 0.3 type-compat (no fk-suffix bonus)
        assert r["left"]["column"] == "username"
        assert r["right"]["column"] == "username"

    # ── 5. Regression: existing GET /schema/{source_id} still works ──

    async def test_existing_schema_endpoint_still_works(self) -> None:
        src = FakeSource(
            "mysql",
            _mk_schema([_res("users", _col("id", "INT", nullable=False), _col("name", "VARCHAR"))]),
        )
        registry = _mk_registry({"mysql": src})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/mysql")

        assert resp.status_code == 200
        data = resp.json()
        assert "resources" in data
        assert len(data["resources"]) == 1
        assert data["resources"][0]["name"] == "users"
        assert len(data["resources"][0]["columns"]) == 2

    # ── 6. Multiple sources, zero → return empty ──

    async def test_zero_sources_returns_empty(self) -> None:
        registry = _mk_registry({})  # type: ignore[arg-type]
        init_app(registry)

        async with _make_client() as client:
            resp = await client.get("/schema/relationships")

        assert resp.status_code == 200
        assert resp.json() == []
