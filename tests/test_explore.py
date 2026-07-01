"""Tests for the Explore API — SSE streaming and suggestion endpoints."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from deepferry.config import LLMConfig as AppLLMConfig
from deepferry.core.llm import GenerateSQLResponse
from deepferry.core.models import ColumnMeta, ResourceMeta, Schema, StructuredResult
from deepferry.web.app import app, init_app


def _mock_source(schema_resources: list[ResourceMeta] | None = None) -> MagicMock:
    source = MagicMock()
    source.source_type = "mysql"
    source.schema_info = AsyncMock(
        return_value=Schema(
            resources=schema_resources
            or [
                ResourceMeta(
                    name="customers",
                    columns=[
                        ColumnMeta(name="id", type="int"),
                        ColumnMeta(name="name", type="varchar"),
                        ColumnMeta(name="email", type="varchar"),
                    ],
                ),
                ResourceMeta(
                    name="orders",
                    columns=[
                        ColumnMeta(name="id", type="int"),
                        ColumnMeta(name="customer_id", type="int"),
                        ColumnMeta(name="total", type="decimal"),
                    ],
                ),
            ],
        )
    )
    source.execute = AsyncMock(
        return_value=StructuredResult(
            columns=[
                ColumnMeta(name="id", type="int"),
                ColumnMeta(name="name", type="varchar"),
            ],
            rows=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            row_count=2,
            execution_time_ms=3.2,
        )
    )
    return source


def _mock_registry(sources: dict[str, MagicMock] | None = None) -> MagicMock:
    reg = MagicMock()
    source_map = sources or {"mysql-main": _mock_source()}
    reg.get = MagicMock(side_effect=lambda sid: source_map[sid])
    reg.list_sources = MagicMock(return_value=[])
    return reg


def _default_llm_config() -> AppLLMConfig:
    return AppLLMConfig(
        provider="test",
        api_key="sk-test",
        model="test-model",
        base_url="http://localhost:1234/v1",
        timeout=5,
    )


def _setup_app(registry: MagicMock, llm_config: AppLLMConfig | None = None) -> None:
    init_app(registry, llm_config=llm_config)


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@pytest.mark.asyncio
async def test_sse_stream_yields_correct_events() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    mock_llm_response = GenerateSQLResponse(
        sql="SELECT * FROM customers LIMIT 10",
        explanation="Fetch all customers",
        model="test-model",
        tokens_used=50,
    )

    with patch(
        "deepferry.web.routes.explore.OpenAICompatibleClient.generate_sql",
        new_callable=AsyncMock,
        return_value=mock_llm_response,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/explore",
                json={
                    "question": "Show me all customers",
                    "source_ids": ["mysql-main"],
                },
            )
            assert response.status_code == 200
            full_text = response.text

    events = _parse_sse_events(full_text)
    steps = [e["step"] for e in events]
    assert steps == [
        "identifying_sources",
        "generating_sql",
        "validating_sql",
        "executing_query",
        "done",
    ]
    done = events[-1]
    assert "result" in done
    assert done["result"]["row_count"] == 2
    assert done["explanation"] == "Fetch all customers"


@pytest.mark.asyncio
async def test_sse_llm_not_configured_returns_503() -> None:
    _setup_app(_mock_registry(), llm_config=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/explore",
            json={"question": "test", "source_ids": ["mysql-main"]},
        )
    assert response.status_code == 503
    detail = response.json()
    assert detail["detail"]["code"] == "LLM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_sse_drop_table_sql_is_blocked() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    dangerous_response = GenerateSQLResponse(
        sql="DROP TABLE customers",
        explanation="Dangerous",
        model="test-model",
        tokens_used=10,
    )

    with patch(
        "deepferry.web.routes.explore.OpenAICompatibleClient.generate_sql",
        new_callable=AsyncMock,
        return_value=dangerous_response,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/explore",
                json={
                    "question": "Drop the customers table",
                    "source_ids": ["mysql-main"],
                },
            )
            assert response.status_code == 200
            full_text = response.text

    events = _parse_sse_events(full_text)
    steps = [e["step"] for e in events]
    assert "error" in steps
    error_event = events[steps.index("error")]
    assert error_event["code"] == "WRITE_BLOCKED"
    assert "done" not in steps


@pytest.mark.asyncio
async def test_sse_source_not_found_emits_error() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    with patch(
        "deepferry.web.routes.explore.OpenAICompatibleClient.generate_sql",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/explore",
                json={
                    "question": "Show me data",
                    "source_ids": ["nonexistent"],
                },
            )
            assert response.status_code == 200
            full_text = response.text

    events = _parse_sse_events(full_text)
    steps = [e["step"] for e in events]
    assert "error" in steps
    assert "done" not in steps


@pytest.mark.asyncio
async def test_sse_llm_unavailable_emits_error() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    with patch(
        "deepferry.web.routes.explore.OpenAICompatibleClient.generate_sql",
        new_callable=AsyncMock,
        side_effect=Exception("Connection refused"),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/explore",
                json={
                    "question": "Show me data",
                    "source_ids": ["mysql-main"],
                },
            )
            assert response.status_code == 200
            full_text = response.text

    events = _parse_sse_events(full_text)
    steps = [e["step"] for e in events]
    assert "error" in steps
    assert "done" not in steps


@pytest.mark.asyncio
async def test_sse_with_conversation_history() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    mock_llm_response = GenerateSQLResponse(
        sql="SELECT name, email FROM customers WHERE email LIKE '%@gmail.com' LIMIT 20",
        explanation="Filtered customers",
        model="test-model",
        tokens_used=60,
    )

    with patch(
        "deepferry.web.routes.explore.OpenAICompatibleClient.generate_sql",
        new_callable=AsyncMock,
        return_value=mock_llm_response,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/explore",
                json={
                    "question": "Show me customers with Gmail addresses",
                    "source_ids": ["mysql-main"],
                    "conversation_history": [
                        {"role": "user", "content": "Show me all customers"},
                        {
                            "role": "assistant",
                            "content": "Here are all customers: Alice, Bob...",
                        },
                    ],
                },
            )
            assert response.status_code == 200
            full_text = response.text

    events = _parse_sse_events(full_text)
    steps = [e["step"] for e in events]
    assert steps[-1] == "done"


@pytest.mark.asyncio
async def test_sse_empty_question_rejected() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/explore",
            json={"question": "", "source_ids": ["mysql-main"]},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_sse_empty_source_ids_rejected() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/explore",
            json={"question": "test", "source_ids": []},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_suggestions_returns_at_least_3_items() -> None:
    _setup_app(_mock_registry(), llm_config=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/explore/suggestions",
            params={"source_ids": "mysql-main"},
        )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) >= 3
    assert all(isinstance(s, str) for s in data["suggestions"])


@pytest.mark.asyncio
async def test_suggestions_empty_source_ids() -> None:
    _setup_app(_mock_registry(), llm_config=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/explore/suggestions",
            params={"source_ids": ""},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["suggestions"] == []


@pytest.mark.asyncio
async def test_suggestions_with_multiple_sources() -> None:
    sources = {
        "mysql-main": _mock_source(),
        "pg-analytics": _mock_source(
            schema_resources=[
                ResourceMeta(
                    name="events",
                    columns=[
                        ColumnMeta(name="id", type="int"),
                        ColumnMeta(name="type", type="varchar"),
                    ],
                ),
            ],
        ),
    }
    _setup_app(_mock_registry(sources=sources), llm_config=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/explore/suggestions",
            params={"source_ids": "mysql-main,pg-analytics"},
        )
    assert response.status_code == 200
    data = response.json()
    assert len(data["suggestions"]) >= 3


@pytest.mark.asyncio
async def test_suggestions_falls_back_when_llm_fails() -> None:
    _setup_app(_mock_registry(), llm_config=_default_llm_config())

    with patch(
        "deepferry.web.routes.explore._generate_llm_suggestions",
        new_callable=AsyncMock,
        side_effect=Exception("LLM unavailable"),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/explore/suggestions",
                params={"source_ids": "mysql-main"},
            )
    assert response.status_code == 200
    data = response.json()
    assert len(data["suggestions"]) >= 3
