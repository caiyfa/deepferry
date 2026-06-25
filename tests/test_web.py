from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from deepferry.core.errors import SourceNotFoundError
from deepferry.core.models import HealthStatus, SourceSummary
from deepferry.web.app import app, init_app


def _mock_registry(
    sources: list[SourceSummary] | None = None,
    health: HealthStatus | None = None,
    get_side_effect: Any = None,
) -> MagicMock:
    reg = MagicMock()
    reg.list_sources.return_value = sources or [
        SourceSummary(id="db", name="DB", type="mysql", health="healthy"),
    ]
    src = MagicMock()
    src.health_check = AsyncMock(
        return_value=health or HealthStatus(ok=True, latency_ms=3.5),
    )
    if get_side_effect is not None:
        reg.get = MagicMock(side_effect=get_side_effect)
    else:
        reg.get = MagicMock(return_value=src)
    return reg


@pytest.fixture
def client():
    init_app(_mock_registry())
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestListSources:
    def test_returns_source_summaries(self):
        sources = [
            SourceSummary(id="db", name="DB", type="mysql", health="healthy"),
            SourceSummary(id="api", name="API", type="http", health="unknown"),
        ]
        init_app(_mock_registry(sources=sources))
        with TestClient(app) as c:
            r = c.get("/config/sources")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert {s["id"] for s in data} == {"db", "api"}
        assert {s["type"] for s in data} == {"mysql", "http"}


class TestSourceTestEndpoint:
    def test_test_source_ok(self):
        health = HealthStatus(ok=True, latency_ms=3.5)
        init_app(_mock_registry(health=health))
        with TestClient(app) as c:
            r = c.post("/config/sources/db/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["latency_ms"] == 3.5

    def test_test_source_not_found_returns_404(self):
        err = SourceNotFoundError(
            code="SOURCE_NOT_FOUND",
            message="Source 'x' is not registered.",
            suggestion="Check config.toml.",
        )
        init_app(_mock_registry(get_side_effect=err))
        with TestClient(app) as c:
            r = c.post("/config/sources/x/test")
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert detail["code"] == "SOURCE_NOT_FOUND"

    def test_test_source_failure_returns_500(self):
        init_app(_mock_registry(get_side_effect=RuntimeError("boom")))
        with TestClient(app) as c:
            r = c.post("/config/sources/db/test")
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert detail["code"] == "TEST_FAILED"
