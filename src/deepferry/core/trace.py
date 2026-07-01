"""OpenTelemetry-aligned span-based execution trace model with SQLite persistence.

Every ``DataSource.execute()`` call opens one execution with one or more spans.
Traces are append-only; writes are fire-and-forget so the tracer never blocks
the query response path.

The span schema mirrors OpenTelemetry naming conventions but traces stay in
SQLite for the desktop execution-detail view — no OTLP export in this phase.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import aiosqlite

TraceEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


# ── Enums ───────────────────────────────────────────────────────────────


class SpanKind(StrEnum):
    """OpenTelemetry-aligned span kind.

    Each value maps to an instrumentation point in the codebase.
    """

    orchestration = "orchestration"
    sql_exec = "sql_exec"
    http_request = "http_request"
    auth = "auth"
    auth_retry = "auth_retry"
    materialize = "materialize"
    join = "join"  # type: ignore[assignment]
    discover = "discover"


class SpanStatus(StrEnum):
    """Span / execution outcome."""

    ok = "ok"
    error = "error"


# ── Models ──────────────────────────────────────────────────────────────


class Span(BaseModel):
    """A single unit of work within an execution.

    Every span belongs to exactly one execution.  Spans form a tree
    via ``parent_span_id`` — roots have ``parent_span_id = None``.
    """

    id: int
    execution_id: int
    parent_span_id: int | None = None
    span_kind: SpanKind
    span_name: str
    source_id: str
    started_at: int  # unix ms
    finished_at: int | None = None
    status: SpanStatus = SpanStatus.ok
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    """Key-value metadata: sql text, http url, row counts, error codes, etc."""


class Execution(BaseModel):
    """Top-level trace container for one ``DataSource.execute()`` call.

    An execution owns a flat list of spans.  The span tree is reconstructed
    from each span's ``parent_span_id`` field.
    """

    id: int
    root_query_id: int | None = None  # FK → query_history.id
    source_id: str
    scenario_id: str | None = None  # FK → scenario correlation
    session_id: str | None = None  # captured from MCP session header
    started_at: int  # unix ms
    finished_at: int | None = None
    status: SpanStatus = SpanStatus.ok
    agent_name: str | None = None
    conversation_id: str | None = None
    source_breakdown_json: str | None = None  # JSON-encoded dict[str, Any] of per-source row counts/metadata
    spans: list[Span] = Field(default_factory=list)


# ── TraceSink ───────────────────────────────────────────────────────────


class TraceSink:
    """Append-only audit trace store backed by SQLite.

    One execution per ``DataSource.execute()`` call.  Executions contain a
    tree of spans.  Writes are fire-and-forget — the tracer never blocks the
    query response.

    Parameters
    ----------
    db : aiosqlite.Connection
        An existing aiosqlite connection (WAL mode enabled, foreign keys on).
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
        self._subscribers: list[TraceEventCallback] = []

    def add_subscriber(self, callback: TraceEventCallback) -> None:
        """Register an async callback invoked after every trace mutation (best-effort, exceptions swallowed)."""
        self._subscribers.append(callback)

    async def _notify(self, event: dict[str, Any]) -> None:
        """Best-effort fan-out to all subscribers; a subscriber exception is logged and swallowed."""
        for cb in self._subscribers:
            with contextlib.suppress(Exception):
                await cb(event)  # trace events must never break the query path

    # ── schema ──────────────────────────────────────────────────────

    @staticmethod
    async def init_schema(db: aiosqlite.Connection) -> None:
        """Create ``trace_executions`` and ``trace_spans`` tables."""
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_executions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                root_query_id   INTEGER,
                source_id       TEXT NOT NULL,
                scenario_id     TEXT,
                session_id      TEXT,
                started_at      INTEGER NOT NULL,
                finished_at     INTEGER,
                status          TEXT NOT NULL DEFAULT 'ok'
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_executions_source
                ON trace_executions(source_id, started_at)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_executions_status
                ON trace_executions(status)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_executions_scenario
                ON trace_executions(scenario_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_executions_session
                ON trace_executions(session_id)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_spans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id    INTEGER NOT NULL,
                parent_span_id  INTEGER,
                span_kind       TEXT NOT NULL,
                span_name       TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                started_at      INTEGER NOT NULL,
                finished_at     INTEGER,
                status          TEXT NOT NULL DEFAULT 'ok',
                attributes      TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (execution_id) REFERENCES trace_executions(id),
                FOREIGN KEY (parent_span_id)  REFERENCES trace_spans(id)
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spans_execution
                ON trace_spans(execution_id)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spans_parent
                ON trace_spans(parent_span_id)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS query_scenarios (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                label        TEXT,
                status       TEXT NOT NULL DEFAULT 'open',
                started_at   INTEGER NOT NULL,
                finished_at  INTEGER,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scenarios_session
                ON query_scenarios(session_id, started_at)
            """
        )
        # ── Schema migration: add new columns if missing ──────────────
        cursor = await db.execute("PRAGMA table_info(trace_executions)")
        existing_cols = {row[1] for row in await cursor.fetchall()}
        for col_name in ("agent_name", "conversation_id", "source_breakdown_json"):
            if col_name not in existing_cols:
                await db.execute(
                    f"ALTER TABLE trace_executions ADD COLUMN {col_name} TEXT"
                )
        await db.commit()

    # ── execution lifecycle ──────────────────────────────────────────

    async def start_execution(
        self,
        source_id: str,
        root_query_id: int | None = None,
        scenario_id: str | None = None,
        session_id: str | None = None,
        agent_name: str | None = None,
        conversation_id: str | None = None,
        source_breakdown: dict[str, Any] | None = None,
    ) -> Execution:
        """Create a new execution and return it with a DB-assigned id.

        Parameters
        ----------
        source_id : str
            The data source that triggered this execution.
        root_query_id : int | None
            Optional FK to a ``query_history`` row for root-cause linking.
        scenario_id : str | None
            Optional scenario UUID this execution belongs to.
        session_id : str | None
            Optional MCP session ID captured from the transport header.
        agent_name : str | None
            Optional agent name for attribution.
        conversation_id : str | None
            Optional conversation ID for grouping.
        source_breakdown : dict[str, Any] | None
            Optional per-source row counts / metadata, serialised to JSON.

        Returns
        -------
        Execution
            The newly created execution with ``id``, ``started_at`` populated.
        """
        now_ms = int(time.time() * 1000)
        breakdown_json = (
            json.dumps(source_breakdown, ensure_ascii=False)
            if source_breakdown is not None
            else None
        )
        cursor = await self._db.execute(
            "INSERT INTO trace_executions "
            "(root_query_id, source_id, scenario_id, session_id, started_at, status, "
            "agent_name, conversation_id, source_breakdown_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                root_query_id,
                source_id,
                scenario_id,
                session_id,
                now_ms,
                SpanStatus.ok.value,
                agent_name,
                conversation_id,
                breakdown_json,
            ),
        )
        await self._db.commit()
        execution_id = cursor.lastrowid
        assert execution_id is not None
        execution = Execution(
            id=execution_id,
            root_query_id=root_query_id,
            source_id=source_id,
            scenario_id=scenario_id,
            session_id=session_id,
            started_at=now_ms,
            agent_name=agent_name,
            conversation_id=conversation_id,
            source_breakdown_json=breakdown_json,
        )
        await self._notify(
            {
                "type": "execution_started",
                "execution_id": execution.id,
                "source_id": execution.source_id,
                "agent_name": execution.agent_name,
                "conversation_id": execution.conversation_id,
                "started_at": execution.started_at,
            }
        )
        return execution

    async def finish_execution(
        self, execution: Execution, status: SpanStatus = SpanStatus.ok
    ) -> None:
        """Mark an execution as completed.

        Updates the database row in-place.  The ``execution`` object is
        mutated so the caller can reuse it without re-fetching.

        Parameters
        ----------
        execution : Execution
            The execution to finish.
        status : SpanStatus
            Final outcome (default ``ok``).
        """
        now_ms = int(time.time() * 1000)
        await self._db.execute(
            "UPDATE trace_executions SET finished_at = ?, status = ? WHERE id = ?",
            (now_ms, status.value, execution.id),
        )
        await self._db.commit()
        execution.finished_at = now_ms
        execution.status = status
        await self._notify(
            {
                "type": "execution_finished",
                "execution_id": execution.id,
                "status": status.value,
                "finished_at": now_ms,
            }
        )

    async def add_span(self, execution: Execution, span: Span) -> Span:
        """Insert a span under *execution* and return it with a DB-assigned id.

        The span's ``execution_id`` and ``started_at`` are set automatically.
        The returned ``Span`` object is a new instance — the input *span* is
        not mutated.

        Parameters
        ----------
        execution : Execution
            The parent execution.
        span : Span
            A partially-populated span (``execution_id``, ``started_at``, and
            ``id`` are ignored — they are set here).

        Returns
        -------
        Span
            The span with DB-assigned ``id``, correct ``execution_id``, and
            ``started_at``.
        """
        now_ms = int(time.time() * 1000)
        attrs_json = json.dumps(span.attributes, ensure_ascii=False)
        cursor = await self._db.execute(
            "INSERT INTO trace_spans "
            "(execution_id, parent_span_id, span_kind, span_name, source_id, "
            "started_at, status, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution.id,
                span.parent_span_id,
                span.span_kind.value,
                span.span_name,
                span.source_id,
                now_ms,
                SpanStatus.ok.value,
                attrs_json,
            ),
        )
        await self._db.commit()
        span_id = cursor.lastrowid
        assert span_id is not None
        new_span = Span(
            id=span_id,
            execution_id=execution.id,
            parent_span_id=span.parent_span_id,
            span_kind=span.span_kind,
            span_name=span.span_name,
            source_id=span.source_id,
            started_at=now_ms,
            attributes=span.attributes,
        )
        await self._notify(
            {
                "type": "span_added",
                "execution_id": execution.id,
                "span_id": new_span.id,
                "span_kind": new_span.span_kind.value,
                "span_name": new_span.span_name,
            }
        )
        return new_span

    async def finish_span(
        self, span: Span, status: SpanStatus = SpanStatus.ok
    ) -> None:
        """Mark a span as completed.

        Updates the database row and mutates the in-memory *span* object.

        Parameters
        ----------
        span : Span
            The span to finish.
        status : SpanStatus
            Final outcome (default ``ok``).
        """
        now_ms = int(time.time() * 1000)
        await self._db.execute(
            "UPDATE trace_spans SET finished_at = ?, status = ? WHERE id = ?",
            (now_ms, status.value, span.id),
        )
        await self._db.commit()
        span.finished_at = now_ms
        span.status = status
        await self._notify(
            {
                "type": "span_finished",
                "span_id": span.id,
                "status": status.value,
            }
        )

    # ── queries ───────────────────────────────────────────────────────

    async def get_execution(self, execution_id: int) -> Execution | None:
        """Fetch an execution with all its spans.

        Populates ``Execution.spans`` with a flat list of spans ordered by
        ``started_at``.  The span tree can be reconstructed from each span's
        ``parent_span_id``.

        Parameters
        ----------
        execution_id : int
            The execution to fetch.

        Returns
        -------
        Execution | None
            The execution with spans, or ``None`` if not found.
        """
        cursor = await self._db.execute(
            "SELECT id, root_query_id, source_id, scenario_id, session_id, "
            "started_at, finished_at, status, agent_name, conversation_id, "
            "source_breakdown_json "
            "FROM trace_executions WHERE id = ?",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        execution = Execution(
            id=row[0],
            root_query_id=row[1],
            source_id=row[2],
            scenario_id=row[3],
            session_id=row[4],
            started_at=row[5],
            finished_at=row[6],
            status=SpanStatus(row[7]),
            agent_name=row[8],
            conversation_id=row[9],
            source_breakdown_json=row[10],
        )

        cursor = await self._db.execute(
            "SELECT id, execution_id, parent_span_id, span_kind, span_name, "
            "source_id, started_at, finished_at, status, attributes "
            "FROM trace_spans WHERE execution_id = ? ORDER BY started_at ASC, id ASC",
            (execution_id,),
        )
        span_rows = await cursor.fetchall()
        execution.spans = [
            Span(
                id=sr[0],
                execution_id=sr[1],
                parent_span_id=sr[2],
                span_kind=SpanKind(sr[3]),
                span_name=sr[4],
                source_id=sr[5],
                started_at=sr[6],
                finished_at=sr[7],
                status=SpanStatus(sr[8]),
                attributes=json.loads(sr[9]) if sr[9] else {},
            )
            for sr in span_rows
        ]
        return execution

    async def list_executions(
        self, source_id: str | None = None, limit: int = 50
    ) -> list[Execution]:
        """List recent executions, optionally filtered by source.

        Returns executions **without** spans (list view, not detail view).
        Use ``get_execution()`` for the full span tree.

        Parameters
        ----------
        source_id : str | None
            Optional filter by data source.
        limit : int
            Max number of executions to return (default 50).

        Returns
        -------
        list[Execution]
            Executions in descending ``started_at`` order, sans spans.
        """
        if source_id is not None:
            cursor = await self._db.execute(
                "SELECT id, root_query_id, source_id, scenario_id, session_id, "
                "started_at, finished_at, status, agent_name, conversation_id, "
                "source_breakdown_json "
                "FROM trace_executions WHERE source_id = ? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (source_id, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, root_query_id, source_id, scenario_id, session_id, "
                "started_at, finished_at, status, agent_name, conversation_id, "
                "source_breakdown_json "
                "FROM trace_executions ORDER BY started_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            Execution(
                id=row[0],
                root_query_id=row[1],
                source_id=row[2],
                scenario_id=row[3],
                session_id=row[4],
                started_at=row[5],
                finished_at=row[6],
                status=SpanStatus(row[7]),
                agent_name=row[8],
                conversation_id=row[9],
                source_breakdown_json=row[10],
            )
            for row in rows
        ]

    async def list_executions_by_scenario(
        self, scenario_id: str, limit: int = 500
    ) -> list[Execution]:
        """List executions belonging to a specific scenario.

        Returns executions **without** spans (list view). Use
        ``get_execution()`` for the full span tree.

        Parameters
        ----------
        scenario_id : str
            The scenario UUID to filter by.
        limit : int
            Max number of executions to return (default 500).

        Returns
        -------
        list[Execution]
            Executions in descending ``started_at`` order, sans spans.
        """
        cursor = await self._db.execute(
            "SELECT id, root_query_id, source_id, scenario_id, session_id, "
            "started_at, finished_at, status, agent_name, conversation_id, "
            "source_breakdown_json "
            "FROM trace_executions WHERE scenario_id = ? "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            (scenario_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            Execution(
                id=row[0],
                root_query_id=row[1],
                source_id=row[2],
                scenario_id=row[3],
                session_id=row[4],
                started_at=row[5],
                finished_at=row[6],
                status=SpanStatus(row[7]),
                agent_name=row[8],
                conversation_id=row[9],
                source_breakdown_json=row[10],
            )
            for row in rows
        ]

    # ── scenario lifecycle ───────────────────────────────────────────

    async def start_scenario(
        self, session_id: str, label: str | None = None
    ) -> str:
        """Open a new investigation scenario and persist it.

        Inserts a row into ``query_scenarios`` with ``status='open'`` and
        returns the newly generated scenario UUID.

        Parameters
        ----------
        session_id : str
            The MCP session ID this scenario belongs to.  Used to group
            scenarios per agent session.
        label : str | None
            Optional human-readable label for the scenario.

        Returns
        -------
        str
            The new scenario UUID (also the table primary key).
        """
        scenario_id = str(uuid.uuid4())
        started_at = int(time.time() * 1000)
        await self._db.execute(
            "INSERT INTO query_scenarios "
            "(id, session_id, label, status, started_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (scenario_id, session_id, label, started_at),
        )
        await self._db.commit()
        return scenario_id

    async def end_scenario(self, scenario_id: str) -> None:
        """Mark a scenario as closed.

        Sets ``finished_at`` to the current unix-ms timestamp and
        ``status='closed'``.  Silent no-op if the scenario does not exist
        (the UPDATE matches zero rows).

        Parameters
        ----------
        scenario_id : str
            The scenario UUID to close.
        """
        now_ms = int(time.time() * 1000)
        await self._db.execute(
            "UPDATE query_scenarios "
            "SET finished_at = ?, status = 'closed' "
            "WHERE id = ?",
            (now_ms, scenario_id),
        )
        await self._db.commit()

    async def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        """Fetch a scenario with its linked execution count.

        Joins ``query_scenarios`` against ``trace_executions`` to compute
        how many executions are attached to the scenario.

        Parameters
        ----------
        scenario_id : str
            The scenario UUID to fetch.

        Returns
        -------
        dict[str, Any] | None
            Scenario fields plus ``execution_count``, or ``None`` if the
            scenario does not exist.
        """
        cursor = await self._db.execute(
            "SELECT s.id, s.session_id, s.label, s.status, s.started_at, "
            "s.finished_at, s.created_at, COUNT(e.id) AS execution_count "
            "FROM query_scenarios s "
            "LEFT JOIN trace_executions e ON e.scenario_id = s.id "
            "WHERE s.id = ? "
            "GROUP BY s.id",
            (scenario_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "session_id": row[1],
            "label": row[2],
            "status": row[3],
            "started_at": row[4],
            "finished_at": row[5],
            "created_at": row[6],
            "execution_count": row[7],
        }

    async def list_scenarios(
        self, session_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List recent scenarios, optionally filtered by session.

        Parameters
        ----------
        session_id : str | None
            Optional MCP session filter.  When ``None`` (default), scenarios
            from all sessions are returned.
        limit : int
            Maximum number of scenarios to return (default 50).

        Returns
        -------
        list[dict[str, Any]]
            Scenarios in descending ``started_at`` order, each enriched
            with an ``execution_count`` from a LEFT JOIN against
            ``trace_executions``.
        """
        sql = (
            "SELECT s.id, s.session_id, s.label, s.status, s.started_at, "
            "s.finished_at, s.created_at, COUNT(e.id) AS execution_count "
            "FROM query_scenarios s "
            "LEFT JOIN trace_executions e ON e.scenario_id = s.id "
            + ("WHERE s.session_id = ? " if session_id is not None else "")
            + "GROUP BY s.id "
            "ORDER BY s.started_at DESC, s.id DESC LIMIT ?"
        )
        if session_id is not None:
            cursor = await self._db.execute(sql, (session_id, limit))
        else:
            cursor = await self._db.execute(sql, (limit,))
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "session_id": row[1],
                "label": row[2],
                "status": row[3],
                "started_at": row[4],
                "finished_at": row[5],
                "created_at": row[6],
                "execution_count": row[7],
            }
            for row in rows
        ]

    async def attach_to_scenario(
        self, execution_id: int, scenario_id: str
    ) -> None:
        """Link an existing execution to a scenario.

        Updates ``trace_executions.scenario_id`` in-place.  Typically called
        after a query succeeds to associate its execution with the active
        scenario.  Silent no-op if the execution row does not exist.

        Parameters
        ----------
        execution_id : int
            The trace execution id to link.
        scenario_id : str
            The target scenario UUID.
        """
        await self._db.execute(
            "UPDATE trace_executions SET scenario_id = ? WHERE id = ?",
            (scenario_id, execution_id),
        )
        await self._db.commit()
