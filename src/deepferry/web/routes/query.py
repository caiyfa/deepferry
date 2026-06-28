"""POST /query — execute a query against a registered data source.

Maps the JSON request body (``sql`` / ``params`` / ``max_rows`` plus optional
``scenario_id`` / ``session_id`` for trace correlation) onto a
``QueryRequest``, delegates to the source, records the result to query history,
and wraps execution in a trace span when a ``TraceSink`` is configured.
"""

from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deepferry.core.db import record_query
from deepferry.core.errors import DataSourceError, SourceNotFoundError
from deepferry.core.models import QueryRequest, StructuredResult
from deepferry.core.trace import Execution, SpanStatus, TraceSink
from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import get_db, get_registry, get_trace_sink

router = APIRouter(prefix="")


class QueryBody(BaseModel):
    """JSON body for ``POST /query``.

    The ``sql`` field maps to ``QueryRequest.statement``.  ``scenario_id`` and
    ``session_id`` are not part of ``QueryRequest`` — they are forwarded to the
    trace sink to correlate the execution with an investigation scenario.
    """

    source_id: str
    statement: str
    params: dict[str, Any] | None = None
    max_rows: int | None = Field(default=None, ge=1)
    scenario_id: str | None = None
    session_id: str | None = None


@router.post("/query")
async def query(
    body: QueryBody,
    registry: SourceRegistry = Depends(get_registry),
    db: aiosqlite.Connection | None = Depends(get_db),
    trace_sink: TraceSink | None = Depends(get_trace_sink),
) -> dict[str, Any]:
    """Execute a SQL query against a data source.

    Returns the ``StructuredResult`` serialised as JSON.  Every successful
    execution is appended to ``query_history`` (when a db connection is
    configured) and wrapped in a trace execution (when a trace sink is
    configured).  Missing sources yield a structured 404; datasource failures
    yield a structured 502; any other error yields a structured 500.
    """
    try:
        source = registry.get(body.source_id)
        request = QueryRequest(
            source_id=body.source_id,
            statement=body.statement,
            params=body.params,
            max_rows=body.max_rows,
        )

        execution: Execution | None = None
        if trace_sink is not None:
            execution = await trace_sink.start_execution(
                source_id=body.source_id,
                scenario_id=body.scenario_id,
                session_id=body.session_id,
            )

        span_status = SpanStatus.ok
        try:
            result: StructuredResult = await source.execute(request)
        except Exception:
            span_status = SpanStatus.error
            raise
        finally:
            if trace_sink is not None and execution is not None:
                await trace_sink.finish_execution(execution, status=span_status)

        if db is not None:
            await record_query(
                db,
                source_id=body.source_id,
                query_text=body.statement,
                row_count=result.row_count,
                status="ok",
                execution_time_ms=result.execution_time_ms,
            )

        response = result.model_dump(mode="json")
        response["source_id"] = body.source_id
        return response
    except SourceNotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    except DataSourceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "QUERY_FAILED",
                "message": str(e),
            },
        ) from e
