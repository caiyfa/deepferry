"""Tests for the Agent Monitor REST API (``/api/agents`` routes).

Uses httpx with an ASGITransport against the real FastAPI app, backed by an
in-memory aiosqlite database seeded with sample trace executions.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite
import httpx
import pytest

from deepferry.core.trace import TraceSink
from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import app, init_app

# ── Test data helpers ────────────────────────────────────────────────────

_NOW_MS = int(time.time() * 1000)
_TODAY_START_MS = _NOW_MS // 86400000 * 86400000


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _seed_db(db: aiosqlite.Connection) -> None:
    """Create the trace schema and insert sample executions."""
    await TraceSink.init_schema(db)

    # Session sess-aaa: 3 executions (2 ok, 1 error), agent "gpt-4"
    # sess-aaa/e1: within the last hour → makes session active; has finished_at for avg
    rows: list[dict[str, Any]] = [
        {
            "session_id": "sess-aaa",
            "source_id": "mysql-main",
            "agent_name": "gpt-4",
            "conversation_id": "conv-1",
            "started_at": _NOW_MS - 600_000,  # 10 min ago
            "finished_at": _NOW_MS - 580_000,  # 20s duration
            "status": "ok",
        },
        {
            "session_id": "sess-aaa",
            "source_id": "pg-analytics",
            "agent_name": "gpt-4",
            "conversation_id": "conv-1",
            "started_at": _NOW_MS - 1_800_000,  # 30 min ago
            "finished_at": _NOW_MS - 1_795_000,  # 5s duration
            "status": "error",
        },
        {
            "session_id": "sess-aaa",
            "source_id": "mysql-main",
            "agent_name": None,
            "conversation_id": None,
            "started_at": _TODAY_START_MS,  # midnight today
            "finished_at": None,
            "status": "ok",
        },
        # Session sess-bbb: 2 executions (all ok), agent "claude-3"
        {
            "session_id": "sess-bbb",
            "source_id": "mysql-main",
            "agent_name": "claude-3",
            "conversation_id": "conv-2",
            "started_at": _NOW_MS - 3_600_000,  # 1 hour ago (borderline active)
            "finished_at": _NOW_MS - 3_590_000,  # 10s duration
            "status": "ok",
        },
        {
            "session_id": "sess-bbb",
            "source_id": "http-api",
            "agent_name": None,
            "conversation_id": None,
            "started_at": _NOW_MS - 7_200_000,  # 2 hours ago
            "finished_at": _NOW_MS - 7_180_000,
            "status": "ok",
        },
    ]

    for r in rows:
        await db.execute(
            """INSERT INTO trace_executions
               (session_id, source_id, agent_name, conversation_id,
                started_at, finished_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                r["session_id"],
                r["source_id"],
                r["agent_name"],
                r["conversation_id"],
                r["started_at"],
                r["finished_at"],
                r["status"],
            ),
        )
    await db.commit()


@pytest.fixture
async def _setup_with_db():
    """Initialise the app with an in-memory trace database."""
    db = await aiosqlite.connect(":memory:")
    await _seed_db(db)
    init_app(SourceRegistry(), db=db)
    yield
    await db.close()


@pytest.fixture
async def _setup_no_db():
    """Initialise the app without a trace database."""
    init_app(SourceRegistry(), db=None)
    yield


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sessions_returns_two_ordered(_setup_with_db) -> None:
    async with _make_client() as client:
        resp = await client.get("/agents/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    assert isinstance(sessions, list)
    assert len(sessions) == 2

    # Most recent first.
    assert sessions[0]["session_id"] == "sess-aaa"
    assert sessions[1]["session_id"] == "sess-bbb"

    # sess-aaa assertions.
    s0 = sessions[0]
    assert s0["agent_name"] == "gpt-4"
    assert s0["execution_count"] == 3
    assert s0["error_count"] == 1
    assert set(s0["sources"]) == {"mysql-main", "pg-analytics"}
    assert s0["last_activity"] is not None

    # sess-bbb assertions.
    s1 = sessions[1]
    assert s1["agent_name"] == "claude-3"
    assert s1["execution_count"] == 2
    assert s1["error_count"] == 0
    assert set(s1["sources"]) == {"mysql-main", "http-api"}


@pytest.mark.asyncio
async def test_get_session_detail_valid(_setup_with_db) -> None:
    async with _make_client() as client:
        resp = await client.get("/agents/sessions/sess-aaa")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["session_id"] == "sess-aaa"
    assert detail["agent_name"] == "gpt-4"
    assert detail["execution_count"] == 3
    assert len(detail["executions"]) == 3

    # Executions ordered by started_at DESC.
    exs = detail["executions"]
    assert exs[0]["source_id"] == "mysql-main"
    assert exs[1]["source_id"] == "pg-analytics"
    assert exs[2]["source_id"] == "mysql-main"

    # Fields present.
    for ex in exs:
        assert "id" in ex
        assert "source_id" in ex
        assert "started_at" in ex
        assert "status" in ex


@pytest.mark.asyncio
async def test_get_session_detail_unknown_404(_setup_with_db) -> None:
    async with _make_client() as client:
        resp = await client.get("/agents/sessions/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_stats(_setup_with_db) -> None:
    async with _make_client() as client:
        resp = await client.get("/agents/stats")
    assert resp.status_code == 200
    stats = resp.json()

    assert stats["total_executions"] == 5
    assert stats["error_rate"] == 0.2  # 1 error / 5 total
    assert stats["today_queries"] >= 1  # the midnight-today row

    # avg_latency: (20000 + 5000 + 10000 + 20000) / 4 = 13750 ms
    # (only 4 have finished_at)
    assert stats["avg_latency"] is not None
    assert abs(stats["avg_latency"] - 13750.0) < 1.0

    # top_sources: mysql-main (3), pg-analytics (1), http-api (1)
    top = stats["top_sources"]
    assert len(top) == 3
    assert top[0] == {"source_id": "mysql-main", "count": 3}
    assert {"source_id": "pg-analytics", "count": 1} in top
    assert {"source_id": "http-api", "count": 1} in top


@pytest.mark.asyncio
async def test_graceful_degradation_no_db(_setup_no_db) -> None:
    async with _make_client() as client:
        resp_sessions = await client.get("/agents/sessions")
    assert resp_sessions.status_code == 200
    assert resp_sessions.json() == []

    async with _make_client() as client:
        resp_stats = await client.get("/agents/stats")
    assert resp_stats.status_code == 200
    stats = resp_stats.json()
    assert stats["total_executions"] == 0
    assert stats["error_rate"] == 0.0
    assert stats["today_queries"] == 0
    assert stats["active_agents"] == 0
    assert stats["avg_latency"] is None
    assert stats["top_sources"] == []

    # Session detail with no DB → 404.
    async with _make_client() as client:
            resp_detail = await client.get("/agents/sessions/sess-aaa")
    assert resp_detail.status_code == 404
    assert resp_detail.json()["detail"]["code"] == "NO_DB"
