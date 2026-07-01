"""Tests for POST /query/analyze — LLM-powered SQL analysis endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from deepferry.config import LLMConfig as AppLLMConfig
from deepferry.core.errors import (
    LLMInvalidSQLError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from deepferry.web.app import app, init_app
from deepferry.web.routes.query import _heuristic_analyze


def _default_llm_config() -> AppLLMConfig:
    return AppLLMConfig(
        provider="test",
        api_key="sk-test",
        model="test-model",
        base_url="http://localhost:1234/v1",
        timeout=5,
    )


def _mock_registry(sources: dict[str, MagicMock] | None = None) -> MagicMock:
    reg = MagicMock()
    source_map = sources or {}
    reg.get = MagicMock(side_effect=lambda sid: source_map[sid])
    return reg


def _setup_app(registry: MagicMock, llm_config: AppLLMConfig | None = None) -> None:
    init_app(registry, llm_config=llm_config)


# ── Direct heuristic tests (no HTTP, no LLM) ─────────────────────────────


class TestHeuristicAnalyze:
    """Unit tests for the rule-based fallback analyzer."""

    def test_select_star_lowers_scores(self) -> None:
        result = _heuristic_analyze("SELECT * FROM users")
        assert result.performance.score <= 55  # 70 - 20 - 15
        assert result.safety.score <= 75  # 90 - 20
        assert result.overall_score < 80
        assert result.model is None

    def test_select_with_limit_scores_higher(self) -> None:
        result = _heuristic_analyze("SELECT id FROM t LIMIT 10")
        assert result.performance.score >= 60  # 70 + 10 - 15
        assert result.safety.score >= 85  # no SELECT *, no multi-statement
        assert result.overall_score > 60
        assert result.model is None

    def test_multiline_improves_readability(self) -> None:
        stmt = "SELECT id,\n       name\nFROM users"
        assert stmt.count("\n") > 0
        result = _heuristic_analyze(stmt)
        assert result.readability.score > 70  # multiline bonus
        assert "Multiline" in result.readability.summary

    def test_all_dimensions_present(self) -> None:
        result = _heuristic_analyze("SELECT 1")
        assert isinstance(result.performance.score, int)
        assert 0 <= result.performance.score <= 100
        assert isinstance(result.safety.score, int)
        assert 0 <= result.safety.score <= 100
        assert isinstance(result.readability.score, int)
        assert 0 <= result.readability.score <= 100
        assert isinstance(result.overall_score, int)
        assert 0 <= result.overall_score <= 100
        assert result.model is None

    def test_clamp_bounds(self) -> None:
        result = _heuristic_analyze("SELECT * FROM t")  # no WHERE, SELECT *
        assert 0 <= result.performance.score <= 100
        assert 0 <= result.safety.score <= 100
        assert 0 <= result.readability.score <= 100
        assert 0 <= result.overall_score <= 100

    def test_suggestions_are_strings(self) -> None:
        result = _heuristic_analyze("SELECT * FROM t")
        for dim in [result.performance, result.safety, result.readability]:
            for s in dim.suggestions:
                assert isinstance(s, str)


# ── HTTP integration tests ───────────────────────────────────────────────


class TestAnalyzeEndpoint:
    """Integration tests for POST /query/analyze."""

    @pytest.mark.asyncio
    async def test_no_llm_config_returns_503(self) -> None:
        _setup_app(_mock_registry(), llm_config=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/query/analyze",
                json={"statement": "SELECT 1", "source_ids": []},
            )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_llm_valid_json_returns_200(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        llm_json = json.dumps({
            "performance": {
                "score": 85,
                "summary": "Good performance.",
                "suggestions": ["Add an index"],
            },
            "safety": {
                "score": 95,
                "summary": "Safe query.",
                "suggestions": [],
            },
            "readability": {
                "score": 70,
                "summary": "Readable.",
                "suggestions": ["Use aliases"],
            },
        })

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            return_value=llm_json,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT id FROM users WHERE id=1", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["performance"]["score"] == 85
        assert body["safety"]["score"] == 95
        assert body["readability"]["score"] == 70
        assert body["overall_score"] == 83  # (85+95+70)//3
        assert body["model"] == "test-model"
        assert body["performance"]["summary"] == "Good performance."
        assert body["performance"]["suggestions"] == ["Add an index"]

    @pytest.mark.asyncio
    async def test_llm_unavailable_error_returns_503(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        async def _raise_unavailable(*args: object, **kwargs: object) -> None:
            raise LLMUnavailableError(
                message="LLM is down",
                suggestion="Try again later",
            )

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            side_effect=_raise_unavailable,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1", "source_ids": []},
                )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "LLM_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_llm_timeout_error_returns_504(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        async def _raise_timeout(*args: object, **kwargs: object) -> None:
            raise LLMTimeoutError(message="Timed out")

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            side_effect=_raise_timeout,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1", "source_ids": []},
                )

        assert resp.status_code == 504
        detail = resp.json()["detail"]
        assert detail["code"] == "LLM_TIMEOUT"

    @pytest.mark.asyncio
    async def test_llm_malformed_json_falls_back_to_heuristic(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            return_value="this is not json at all {{{",
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT * FROM t", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] is None  # heuristic fallback has no model
        assert isinstance(body["performance"]["score"], int)
        assert isinstance(body["safety"]["score"], int)
        assert isinstance(body["readability"]["score"], int)
        assert isinstance(body["overall_score"], int)

    @pytest.mark.asyncio
    async def test_llm_invalid_sql_error_falls_back_to_heuristic(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        async def _raise_invalid_sql(*args: object, **kwargs: object) -> None:
            raise LLMInvalidSQLError(message="Bad response")

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            side_effect=_raise_invalid_sql,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT id FROM users LIMIT 50", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] is None
        assert body["performance"]["score"] > 50  # LIMIT helps

    @pytest.mark.asyncio
    async def test_generic_exception_falls_back_to_heuristic(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        async def _raise_runtime(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Unexpected crash")

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            side_effect=_raise_runtime,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] is None
        # Scores should all be clamped within 0-100
        assert 0 <= body["performance"]["score"] <= 100
        assert 0 <= body["safety"]["score"] <= 100
        assert 0 <= body["readability"]["score"] <= 100
        assert 0 <= body["overall_score"] <= 100

    @pytest.mark.asyncio
    async def test_missing_dimension_in_json_defaults(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        # JSON with only a partial response
        llm_json = json.dumps({
            "performance": {"score": 90, "summary": "Fast.", "suggestions": []},
            # safety and readability missing
        })

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            return_value=llm_json,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["performance"]["score"] == 90
        # Missing dimensions default to score=50
        assert body["safety"]["score"] == 50
        assert body["readability"]["score"] == 50
        assert body["overall_score"] == (90 + 50 + 50) // 3

    @pytest.mark.asyncio
    async def test_llm_response_empty_string_falls_back(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            return_value="",
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1", "source_ids": []},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] is None

    @pytest.mark.asyncio
    async def test_default_source_ids_are_empty(self) -> None:
        _setup_app(_mock_registry(), llm_config=_default_llm_config())

        llm_json = json.dumps({
            "performance": {"score": 80, "summary": "Good", "suggestions": []},
            "safety": {"score": 90, "summary": "Safe", "suggestions": []},
            "readability": {"score": 70, "summary": "OK", "suggestions": []},
        })

        with patch(
            "deepferry.web.routes.query.OpenAICompatibleClient.complete",
            new_callable=AsyncMock,
            return_value=llm_json,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/query/analyze",
                    json={"statement": "SELECT 1"},
                )

        assert resp.status_code == 200
