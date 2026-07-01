"""Agent monitor REST endpoints.

``GET /api/agents/sessions`` — aggregated session list for the monitor grid.
``GET /api/agents/sessions/{session_id}`` — recent executions for one session.
``GET /api/agents/stats`` — aggregate statistics for the summary bar.

All endpoints read directly from the ``trace_executions`` table via
``get_db()``.  When tracing is disabled (``db is None``) the endpoints
degrade gracefully with empty / zero responses.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deepferry.web.app import get_db

router = APIRouter(prefix="/agents", tags=["agents"])

# ── Response models ──────────────────────────────────────────────────────


class SessionSummary(BaseModel):
    """One row for the agent monitor session list."""

    session_id: str
    agent_name: str | None = None
    execution_count: int
    last_activity: int | None = None  # unix ms
    error_count: int = 0
    sources: list[str] = Field(default_factory=list)


class SessionDetail(BaseModel):
    """Full session detail with recent executions."""

    session_id: str
    agent_name: str | None = None
    executions: list[dict[str, Any]]
    execution_count: int


class AgentStats(BaseModel):
    """Aggregate statistics for the monitor summary bar.

    Field names match the API contract documented in ``tasks-backend.md``.
    """

    active_agents: int
    today_queries: int
    avg_latency: float | None = None
    error_rate: float = 0.0
    total_executions: int = 0
    top_sources: list[dict[str, Any]] = Field(default_factory=list)


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    db: aiosqlite.Connection | None = Depends(get_db),
) -> list[SessionSummary]:
    """List sessions ordered by most recent activity (limit 50).

    Returns an empty list when tracing is disabled.
    """
    if db is None:
        return []

    db.row_factory = aiosqlite.Row
    cursor = await db.execute(
        """
        SELECT
            session_id,
            (SELECT agent_name FROM trace_executions e2
             WHERE e2.session_id = e.session_id AND e2.agent_name IS NOT NULL
             ORDER BY e2.started_at DESC LIMIT 1) AS agent_name,
            COUNT(*)                                                    AS execution_count,
            MAX(started_at)                                             AS last_activity,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)          AS error_count,
            GROUP_CONCAT(DISTINCT source_id)                            AS sources
        FROM trace_executions e
        WHERE session_id IS NOT NULL
        GROUP BY session_id
        ORDER BY last_activity DESC
        LIMIT 50
        """
    )
    rows = await cursor.fetchall()

    return [
        SessionSummary(
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            execution_count=row["execution_count"],
            last_activity=row["last_activity"],
            error_count=row["error_count"],
            sources=row["sources"].split(",") if row["sources"] else [],
        )
        for row in rows
    ]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> SessionDetail:
    """Fetch a single session's detail with its recent executions.

    Returns 404 when tracing is disabled or the session is not found.
    """
    if db is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_DB", "message": "Tracing is disabled."},
        )

    db.row_factory = aiosqlite.Row

    # Fetch recent executions for this session.
    cursor = await db.execute(
        """
        SELECT
            id,
            source_id,
            started_at,
            finished_at,
            status,
            agent_name,
            conversation_id
        FROM trace_executions
        WHERE session_id = ?
        ORDER BY started_at DESC
        LIMIT 100
        """,
        (session_id,),
    )
    rows = await cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SESSION_NOT_FOUND",
                "message": f"Session {session_id} not found.",
            },
        )

    executions: list[dict[str, Any]] = [
        {
            "id": row["id"],
            "source_id": row["source_id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "agent_name": row["agent_name"],
            "conversation_id": row["conversation_id"],
        }
        for row in rows
    ]

    # Extract agent_name from the most recent execution that has one.
    agent_name: str | None = None
    for ex in executions:
        if ex["agent_name"] is not None:
            agent_name = ex["agent_name"]
            break

    return SessionDetail(
        session_id=session_id,
        agent_name=agent_name,
        executions=executions,
        execution_count=len(executions),
    )


@router.get("/stats")
async def get_stats(
    db: aiosqlite.Connection | None = Depends(get_db),
) -> AgentStats:
    """Return aggregate statistics for the agent monitor summary bar.

    Returns all-zero stats when tracing is disabled.
    """
    if db is None:
        return AgentStats(
            active_agents=0,
            today_queries=0,
            avg_latency=None,
            error_rate=0.0,
            total_executions=0,
            top_sources=[],
        )

    db.row_factory = aiosqlite.Row

    now_ms = int(time.time() * 1000)
    today_start_ms = now_ms // 86400000 * 86400000
    one_hour_ago_ms = now_ms - 3_600_000

    # total_executions & error_count
    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM trace_executions")
    row = await cursor.fetchone()
    total_executions: int = row["cnt"] if row else 0

    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM trace_executions WHERE status = 'error'"
    )
    row = await cursor.fetchone()
    error_count: int = row["cnt"] if row else 0

    # today_executions
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM trace_executions WHERE started_at >= ?",
        (today_start_ms,),
    )
    row = await cursor.fetchone()
    today_executions: int = row["cnt"] if row else 0

    # active_sessions (sessions with activity in the last hour)
    cursor = await db.execute(
        """
        SELECT COUNT(*) AS cnt FROM (
            SELECT session_id, MAX(started_at) AS last_ts
            FROM trace_executions
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            HAVING last_ts >= ?
        )
        """,
        (one_hour_ago_ms,),
    )
    row = await cursor.fetchone()
    active_sessions: int = row["cnt"] if row else 0

    # avg_duration_ms
    cursor = await db.execute(
        """
        SELECT AVG(finished_at - started_at) AS avg_dur
        FROM trace_executions
        WHERE finished_at IS NOT NULL
        """
    )
    row = await cursor.fetchone()
    avg_duration_ms: float | None = float(row["avg_dur"]) if row and row["avg_dur"] is not None else None

    # top_sources
    cursor = await db.execute(
        """
        SELECT source_id, COUNT(*) AS cnt
        FROM trace_executions
        GROUP BY source_id
        ORDER BY cnt DESC
        LIMIT 5
        """
    )
    top_rows = await cursor.fetchall()
    top_sources: list[dict[str, Any]] = [
        {"source_id": r["source_id"], "count": r["cnt"]} for r in top_rows
    ]

    return AgentStats(
        active_agents=active_sessions,
        today_queries=today_executions,
        avg_latency=avg_duration_ms,
        error_rate=error_count / total_executions if total_executions > 0 else 0.0,
        total_executions=total_executions,
        top_sources=top_sources,
    )
