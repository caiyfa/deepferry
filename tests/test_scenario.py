"""Unit tests for scenario correlation tools.

Covers start_scenario, end_scenario, TraceSink scenario_id support,
and list_executions_by_scenario.
"""

from __future__ import annotations

import uuid

import aiosqlite
import pytest

from deepferry.core.trace import TraceSink
from deepferry.mcp_server.tools import end_scenario, start_scenario

# ── helpers ──────────────────────────────────────────────────────────────


@pytest.fixture
async def sink() -> TraceSink:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await TraceSink.init_schema(db)
    yield TraceSink(db)
    await db.close()


# ── start_scenario ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_scenario_returns_id() -> None:
    result = await start_scenario()
    assert "scenario_id" in result
    assert "label" in result
    assert isinstance(result["scenario_id"], str)
    # Must be a valid UUID
    uuid.UUID(result["scenario_id"])
    assert result["label"] is None


@pytest.mark.asyncio
async def test_start_scenario_with_label() -> None:
    result = await start_scenario(label="Q3 refund investigation")
    assert result["scenario_id"] is not None
    assert result["label"] == "Q3 refund investigation"
    uuid.UUID(result["scenario_id"])


@pytest.mark.asyncio
async def test_start_scenario_unique_ids() -> None:
    result1 = await start_scenario()
    result2 = await start_scenario()
    assert result1["scenario_id"] != result2["scenario_id"]


# ── end_scenario ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_scenario() -> None:
    sid = str(uuid.uuid4())
    result = await end_scenario(sid)
    assert result["scenario_id"] == sid
    assert result["status"] == "closed"


# ── TraceSink scenario_id support ────────────────────────────────────────


@pytest.mark.asyncio
async def test_execution_with_scenario_id(sink: TraceSink) -> None:
    scenario_id = str(uuid.uuid4())
    session_id = "mcp-session-abc123"

    execution = await sink.start_execution(
        source_id="test-db",
        scenario_id=scenario_id,
        session_id=session_id,
    )

    assert execution.scenario_id == scenario_id
    assert execution.session_id == session_id
    assert execution.id is not None

    # Fetch it back and verify persistence
    fetched = await sink.get_execution(execution.id)
    assert fetched is not None
    assert fetched.scenario_id == scenario_id
    assert fetched.session_id == session_id


@pytest.mark.asyncio
async def test_execution_without_scenario_id(sink: TraceSink) -> None:
    execution = await sink.start_execution(source_id="test-db")

    assert execution.scenario_id is None
    assert execution.session_id is None

    fetched = await sink.get_execution(execution.id)
    assert fetched is not None
    assert fetched.scenario_id is None
    assert fetched.session_id is None


@pytest.mark.asyncio
async def test_list_executions_by_scenario(sink: TraceSink) -> None:
    scenario_a = str(uuid.uuid4())
    scenario_b = str(uuid.uuid4())

    # Create 3 executions in scenario A
    for _ in range(3):
        await sink.start_execution(source_id="db-1", scenario_id=scenario_a)

    # Create 2 executions in scenario B
    for _ in range(2):
        await sink.start_execution(source_id="db-2", scenario_id=scenario_b)

    # Create 1 execution with no scenario
    await sink.start_execution(source_id="db-3")

    # List scenario A
    result_a = await sink.list_executions_by_scenario(scenario_a)
    assert len(result_a) == 3
    for ex in result_a:
        assert ex.scenario_id == scenario_a

    # List scenario B
    result_b = await sink.list_executions_by_scenario(scenario_b)
    assert len(result_b) == 2

    # List unknown scenario
    result_empty = await sink.list_executions_by_scenario(str(uuid.uuid4()))
    assert len(result_empty) == 0


@pytest.mark.asyncio
async def test_list_executions_by_scenario_ordering(sink: TraceSink) -> None:
    scenario_id = str(uuid.uuid4())

    e1 = await sink.start_execution(source_id="s1", scenario_id=scenario_id)
    e2 = await sink.start_execution(source_id="s2", scenario_id=scenario_id)
    e3 = await sink.start_execution(source_id="s3", scenario_id=scenario_id)

    result = await sink.list_executions_by_scenario(scenario_id)
    assert len(result) == 3
    # Should be descending by started_at
    assert result[0].id == e3.id
    assert result[1].id == e2.id
    assert result[2].id == e1.id


# ── schema verification ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_schema_includes_scenario_columns() -> None:
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await TraceSink.init_schema(db)

        cursor = await db.execute("PRAGMA table_info(trace_executions)")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert "scenario_id" in cols
        assert "session_id" in cols

        # Verify indexes
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='trace_executions'"
        )
        indexes = {row["name"] for row in await cursor.fetchall()}
        assert "idx_executions_scenario" in indexes
        assert "idx_executions_session" in indexes
