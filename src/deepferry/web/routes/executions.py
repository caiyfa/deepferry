"""GET /executions — trace execution list and detail views.

* ``GET /executions`` lists recent executions (optionally filtered by source).
* ``GET /executions/{execution_id}`` fetches a single execution with its span
  tree.

Both endpoints return an empty result (or 404 for the detail view) when no
trace sink is configured.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from deepferry.core.trace import TraceSink
from deepferry.web.app import get_trace_sink

router = APIRouter(prefix="")


@router.get("/executions")
async def list_executions(
    source_id: str | None = None,
    limit: int = 50,
    trace_sink: TraceSink | None = Depends(get_trace_sink),
) -> list[dict[str, Any]]:
    """List recent trace executions, optionally filtered by source.

    Returns an empty list when no trace sink is configured; any other failure
    yields a structured 500.
    """
    try:
        if trace_sink is None:
            return []
        executions = await trace_sink.list_executions(source_id, limit)
        return [execution.model_dump(mode="json") for execution in executions]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "EXECUTIONS_LIST_FAILED",
                "message": str(e),
            },
        ) from e


@router.get("/executions/{execution_id}")
async def get_execution(
    execution_id: int,
    trace_sink: TraceSink | None = Depends(get_trace_sink),
) -> dict[str, Any]:
    """Fetch a single execution with its span tree.

    Returns a structured 404 when no trace sink is configured or the execution
    is not found; any other failure yields a structured 500.
    """
    if trace_sink is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TRACE_NOT_CONFIGURED",
                "message": "Trace sink is not configured.",
            },
        )

    try:
        execution = await trace_sink.get_execution(execution_id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "EXECUTION_FETCH_FAILED",
                "message": str(e),
            },
        ) from e

    if execution is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "EXECUTION_NOT_FOUND",
                "message": f"Execution {execution_id} not found.",
            },
        )

    return execution.model_dump(mode="json")
