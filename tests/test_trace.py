"""Unit tests for core.trace — TraceSink and trace models.

Covers schema initialisation, execution lifecycle, span lifecycle,
tree reconstruction, attribute round-tripping, filtering, and
FK linking to query_history.
"""

from __future__ import annotations

import json

import aiosqlite
import pytest

from deepferry.core.trace import (
    Execution,
    Span,
    SpanKind,
    SpanStatus,
    TraceSink,
)


# ── helpers ─────────────────────────────────────────────────────────────


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return (await cursor.fetchone()) is not None


@pytest.fixture
async def sink() -> TraceSink:
    """Create an in-memory TraceSink with schema initialised."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await TraceSink.init_schema(db)
    yield TraceSink(db)
    await db.close()


# ── schema tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_schema_creates_tables() -> None:
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await TraceSink.init_schema(db)

        assert await _table_exists(db, "trace_executions")
        assert await _table_exists(db, "trace_spans")

        # Verify trace_executions columns
        cursor = await db.execute("PRAGMA table_info(trace_executions)")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert cols >= {"id", "root_query_id", "source_id", "started_at", "finished_at", "status"}

        # Verify trace_spans columns
        cursor = await db.execute("PRAGMA table_info(trace_spans)")
        cols = {row["name"] for row in await cursor.fetchall()}
        assert cols >= {
            "id", "execution_id", "parent_span_id", "span_kind",
            "span_name", "source_id", "started_at", "finished_at",
            "status", "attributes",
        }

        # Verify indexes exist
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name IN ('trace_executions', 'trace_spans')"
        )
        indexes = {row["name"] for row in await cursor.fetchall()}
        assert "idx_executions_source" in indexes
        assert "idx_executions_status" in indexes
        assert "idx_spans_execution" in indexes
        assert "idx_spans_parent" in indexes


# ── execution lifecycle ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_execution(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.orders")

    assert exec_.id > 0
    assert exec_.source_id == "mysql.orders"
    assert exec_.root_query_id is None
    assert exec_.started_at > 0
    assert exec_.finished_at is None
    assert exec_.status == SpanStatus.ok
    assert exec_.spans == []


@pytest.mark.asyncio
async def test_start_and_finish_execution(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("pg.users")
    await sink.finish_execution(exec_, SpanStatus.ok)

    assert exec_.finished_at is not None
    assert exec_.finished_at >= exec_.started_at
    assert exec_.status == SpanStatus.ok

    # Verify persisted
    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.finished_at is not None
    assert fetched.status == SpanStatus.ok


@pytest.mark.asyncio
async def test_finish_execution_error(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("http.api")
    await sink.finish_execution(exec_, SpanStatus.error)

    assert exec_.status == SpanStatus.error
    assert exec_.finished_at is not None

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.status == SpanStatus.error


@pytest.mark.asyncio
async def test_execution_with_root_query_id(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.inventory", root_query_id=42)

    assert exec_.root_query_id == 42

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.root_query_id == 42


# ── span lifecycle ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_span(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("pg.users")
    span = await sink.add_span(
        exec_,
        Span(
            id=0,  # placeholder — overwritten
            execution_id=999,  # placeholder — overwritten
            span_kind=SpanKind.sql_exec,
            span_name="pg.users.execute",
            source_id="pg.users",
            started_at=0,  # placeholder — overwritten
            attributes={"sql": "SELECT 1"},
        ),
    )

    assert span.id > 0
    assert span.execution_id == exec_.id
    assert span.started_at > 0
    assert span.finished_at is None
    assert span.status == SpanStatus.ok
    assert span.parent_span_id is None
    assert span.span_kind == SpanKind.sql_exec
    assert span.span_name == "pg.users.execute"
    assert span.source_id == "pg.users"
    assert span.attributes == {"sql": "SELECT 1"}


@pytest.mark.asyncio
async def test_add_and_finish_span(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.orders")
    span = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            span_kind=SpanKind.sql_exec,
            span_name="query",
            source_id="mysql.orders",
            started_at=0,
        ),
    )
    await sink.finish_span(span, SpanStatus.ok)

    assert span.finished_at is not None
    assert span.finished_at >= span.started_at
    assert span.status == SpanStatus.ok

    # Verify persisted
    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert len(fetched.spans) == 1
    assert fetched.spans[0].finished_at is not None
    assert fetched.spans[0].status == SpanStatus.ok


@pytest.mark.asyncio
async def test_finish_span_error(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("http.api")
    span = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            span_kind=SpanKind.http_request,
            span_name="GET /users",
            source_id="http.api",
            started_at=0,
        ),
    )
    await sink.finish_span(span, SpanStatus.error)

    assert span.status == SpanStatus.error

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.spans[0].status == SpanStatus.error


# ── span tree ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_span_tree_reconstruction(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("orch.pipeline")

    root = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            span_kind=SpanKind.orchestration,
            span_name="pipeline",
            source_id="orch.pipeline",
            started_at=0,
        ),
    )

    step1 = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            parent_span_id=root.id,
            span_kind=SpanKind.sql_exec,
            span_name="step1.query_users",
            source_id="mysql.users",
            started_at=0,
        ),
    )

    step1_child = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            parent_span_id=step1.id,
            span_kind=SpanKind.auth_retry,
            span_name="retry.auth",
            source_id="mysql.users",
            started_at=0,
            attributes={"trigger_status": 401},
        ),
    )

    step2 = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            parent_span_id=root.id,
            span_kind=SpanKind.http_request,
            span_name="step2.GET /orders",
            source_id="http.orders",
            started_at=0,
        ),
    )

    await sink.finish_execution(exec_)

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert len(fetched.spans) == 4

    # Verify parent linkage
    spans_by_id = {s.id: s for s in fetched.spans}

    root_fetched = spans_by_id[root.id]
    assert root_fetched.parent_span_id is None
    assert root_fetched.span_kind == SpanKind.orchestration

    step1_fetched = spans_by_id[step1.id]
    assert step1_fetched.parent_span_id == root.id

    child_fetched = spans_by_id[step1_child.id]
    assert child_fetched.parent_span_id == step1.id

    step2_fetched = spans_by_id[step2.id]
    assert step2_fetched.parent_span_id == root.id


@pytest.mark.asyncio
async def test_spans_ordered_by_started_at(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.test")
    # Add spans that should be returned in insertion order (which maps
    # to started_at order since each gets a current timestamp).
    await sink.add_span(
        exec_,
        Span(
            id=0, execution_id=0,
            span_kind=SpanKind.sql_exec, span_name="first",
            source_id="mysql.test", started_at=0,
        ),
    )
    await sink.add_span(
        exec_,
        Span(
            id=0, execution_id=0,
            span_kind=SpanKind.sql_exec, span_name="second",
            source_id="mysql.test", started_at=0,
        ),
    )

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert len(fetched.spans) == 2
    assert fetched.spans[0].span_name == "first"
    assert fetched.spans[1].span_name == "second"


# ── attributes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_span_attributes_roundtrip(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("pg.analytics")
    attrs: dict[str, str | int | float | bool] = {
        "sql": "SELECT COUNT(*) FROM orders WHERE status = 'active'",
        "row_count": 1500,
        "dialect": "postgresql",
        "cached": True,
        "latency_pct": 99.5,
    }
    span = await sink.add_span(
        exec_,
        Span(
            id=0,
            execution_id=0,
            span_kind=SpanKind.sql_exec,
            span_name="analytics.query",
            source_id="pg.analytics",
            started_at=0,
            attributes=attrs,
        ),
    )

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    fetched_span = fetched.spans[0]
    assert fetched_span.attributes == attrs
    assert fetched_span.attributes["row_count"] == 1500
    assert fetched_span.attributes["cached"] is True


@pytest.mark.asyncio
async def test_span_attributes_empty_default(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.test")
    span = await sink.add_span(
        exec_,
        Span(
            id=0, execution_id=0,
            span_kind=SpanKind.sql_exec, span_name="query",
            source_id="mysql.test", started_at=0,
            # attributes not set — should default to {}
        ),
    )
    assert span.attributes == {}

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.spans[0].attributes == {}


# ═══════════════════════════════════════════════════════════════════════
# Handling & robustness
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_span_attributes_special_chars(sink: TraceSink) -> None:
    """Attributes containing quotes, unicode, and newlines round-trip correctly."""
    exec_ = await sink.start_execution("mysql.test")
    attrs: dict[str, str | int | float | bool] = {
        "unicode": "こんにちは",
        "quotes": 'he said "hello"',
        "newlines": "line1\nline2",
        "backslash": r"c:\path\to\file",
    }
    span = await sink.add_span(
        exec_,
        Span(
            id=0, execution_id=0,
            span_kind=SpanKind.sql_exec, span_name="test",
            source_id="mysql.test", started_at=0,
            attributes=attrs,
        ),
    )

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.spans[0].attributes == attrs


# ── list executions ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_executions_all(sink: TraceSink) -> None:
    for i in range(5):
        await sink.start_execution(f"source.{i}")

    results = await sink.list_executions(limit=10)
    assert len(results) == 5
    # All 5 sources are present (exact order can vary due to sub-ms timestamps)
    source_ids = {r.source_id for r in results}
    assert source_ids == {f"source.{i}" for i in range(5)}
    # Most recent should be among the last-inserted (highest id ≈ most recent)
    assert results[0].id == max(r.id for r in results)


@pytest.mark.asyncio
async def test_list_executions_filtered(sink: TraceSink) -> None:
    await sink.start_execution("mysql.orders")
    await sink.start_execution("pg.users")
    await sink.start_execution("mysql.inventory")
    await sink.start_execution("http.api")

    mysql_results = await sink.list_executions(source_id="mysql.orders")
    assert len(mysql_results) == 1
    assert mysql_results[0].source_id == "mysql.orders"

    none_results = await sink.list_executions(source_id="nonexistent")
    assert len(none_results) == 0


@pytest.mark.asyncio
async def test_list_executions_limit(sink: TraceSink) -> None:
    for i in range(10):
        await sink.start_execution(f"source.{i}")

    results = await sink.list_executions(limit=3)
    assert len(results) == 3


# ── get_execution edge cases ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_execution_not_found(sink: TraceSink) -> None:
    result = await sink.get_execution(9999)
    assert result is None


@pytest.mark.asyncio
async def test_get_execution_no_spans(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("mysql.test")
    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert fetched.spans == []


# ── multiple spans of different kinds ───────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_span_kinds(sink: TraceSink) -> None:
    exec_ = await sink.start_execution("orch.flow")
    kinds = [
        SpanKind.orchestration,
        SpanKind.auth,
        SpanKind.sql_exec,
        SpanKind.http_request,
        SpanKind.auth_retry,
        SpanKind.discover,
        SpanKind.materialize,
        SpanKind.join,
    ]
    for i, kind in enumerate(kinds):
        await sink.add_span(
            exec_,
            Span(
                id=0, execution_id=0,
                span_kind=kind, span_name=f"span_{i}",
                source_id="orch.flow", started_at=0,
            ),
        )

    fetched = await sink.get_execution(exec_.id)
    assert fetched is not None
    assert len(fetched.spans) == len(kinds)
    fetched_kinds = [s.span_kind for s in fetched.spans]
    assert set(fetched_kinds) == set(kinds)


# ── Timestamp realism ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timestamps_are_unix_ms() -> None:
    """Timestamps must be in the Unix-millisecond range, not seconds."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        await TraceSink.init_schema(db)
        sink = TraceSink(db)

        exec_ = await sink.start_execution("mysql.test")
        # Unix ms for current time should be ~1.7e12, not ~1.7e9
        assert exec_.started_at > 1_700_000_000_000

        span = await sink.add_span(
            exec_,
            Span(
                id=0, execution_id=0,
                span_kind=SpanKind.sql_exec, span_name="q",
                source_id="mysql.test", started_at=0,
            ),
        )
        assert span.started_at > 1_700_000_000_000


# ── idempotency ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_schema_idempotent(sink: TraceSink) -> None:
    """Calling init_schema twice must not raise."""
    await TraceSink.init_schema(sink._db)
    await TraceSink.init_schema(sink._db)
    # If we got here without an exception, it's idempotent.
    assert True
