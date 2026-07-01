"""POST /query — execute a query against a registered data source.
POST /query/analyze — LLM-powered SQL analysis (performance, safety, readability).

Maps the JSON request body (``sql`` / ``params`` / ``max_rows`` plus optional
``scenario_id`` / ``session_id`` for trace correlation) onto a
``QueryRequest``, delegates to the source, records the result to query history,
and wraps execution in a trace span when a ``TraceSink`` is configured.
"""

import json
import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deepferry.config import (
    LLMConfig as AppLLMConfig,  # noqa: TC001  # FastAPI resolves type hints at runtime
)
from deepferry.core.db import record_query
from deepferry.core.errors import (
    DataSourceError,
    LLMInvalidSQLError,
    LLMTimeoutError,
    LLMUnavailableError,
    SourceNotFoundError,
)
from deepferry.core.llm import (
    LLMClient,
    OpenAICompatibleClient,
)
from deepferry.core.llm import (
    LLMConfig as CoreLLMConfig,
)
from deepferry.core.models import QueryRequest, StructuredResult
from deepferry.core.trace import Execution, SpanStatus, TraceSink
from deepferry.datasources.registry import SourceRegistry
from deepferry.web.app import get_db, get_llm_config, get_registry, get_trace_sink

logger = logging.getLogger(__name__)

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


class AnalyzeRequest(BaseModel):
    """JSON body for ``POST /query/analyze``."""

    statement: str
    source_ids: list[str] = []


class AnalyzeDimension(BaseModel):
    """A single dimension of SQL analysis (performance, safety, or readability)."""

    score: int
    summary: str
    suggestions: list[str] = []


class AnalyzeResponse(BaseModel):
    """Response for ``POST /query/analyze``."""

    performance: AnalyzeDimension
    safety: AnalyzeDimension
    readability: AnalyzeDimension
    overall_score: int
    model: str | None = None


# ── LLM client factory (mirrors explore.py) ──────────────────────────────


def _build_llm_client(config: AppLLMConfig) -> OpenAICompatibleClient:
    """Create an OpenAI-compatible LLM client from the application-level config.

    Converts ``deepferry.config.LLMConfig`` → ``deepferry.core.llm.LLMConfig``
    and instantiates the client.
    """
    core_config = CoreLLMConfig(
        provider=config.provider,
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        timeout=config.timeout,
    )
    return OpenAICompatibleClient(core_config)


# ── Heuristic fallback analysis ──────────────────────────────────────────


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    """Clamp *value* to [``low``, ``high``]."""
    return max(low, min(high, value))


def _heuristic_analyze(statement: str) -> AnalyzeResponse:
    """Rule-based SQL analysis used when the LLM is unavailable or fails.

    Produces a basic assessment across performance, safety, and readability
    dimensions using simple heuristics on the statement text.
    """
    stmt = statement.strip()
    stmt_upper = stmt.upper()

    # ── Performance ──
    perf_score = 70
    perf_reasons: list[str] = []
    has_select_star = "SELECT *" in stmt_upper
    if "LIMIT" in stmt_upper:
        perf_score += 10
        perf_reasons.append("LIMIT clause present (+10)")
    if has_select_star:
        perf_score -= 20
        perf_reasons.append("SELECT * detected — may fetch unnecessary columns (-20)")
    if "FROM" in stmt_upper and "WHERE" not in stmt_upper:
        perf_score -= 15
        perf_reasons.append("No WHERE clause — full table scan risk (-15)")
    perf_score = _clamp(perf_score)

    # ── Safety ──
    safety_score = 90
    safety_reasons: list[str] = []
    if ";" in stmt:
        safety_score -= 30
        safety_reasons.append("Multi-statement detected (-30)")
    if "SELECT *" in stmt_upper:
        safety_score -= 20
        safety_reasons.append("Broad column surface (SELECT *) (-20)")
    safety_score = _clamp(safety_score)

    # ── Readability ──
    readability_score = 70
    readability_reasons: list[str] = []
    if "\n" in stmt:
        readability_score += 15
        readability_reasons.append("Multiline formatting (+15)")
    if len(stmt) > 500:
        readability_score -= 10
        readability_reasons.append("Very long statement (>500 chars) (-10)")
    readability_score = _clamp(readability_score)

    overall = (perf_score + safety_score + readability_score) // 3

    def _summary(label: str, reasons: list[str]) -> str:
        if not reasons:
            return f"{label} assessment based on basic heuristics."
        return f"{label}: {', '.join(reasons)}."

    return AnalyzeResponse(
        performance=AnalyzeDimension(
            score=perf_score,
            summary=_summary("Performance", perf_reasons),
            suggestions=perf_reasons,
        ),
        safety=AnalyzeDimension(
            score=safety_score,
            summary=_summary("Safety", safety_reasons),
            suggestions=safety_reasons,
        ),
        readability=AnalyzeDimension(
            score=readability_score,
            summary=_summary("Readability", readability_reasons),
            suggestions=readability_reasons,
        ),
        overall_score=overall,
        model=None,
    )


# ── System prompt for LLM-based analysis ─────────────────────────────────

_ANALYZE_SYSTEM_PROMPT = (
    "You are a SQL analysis expert. Analyze the given SQL statement for "
    "performance, safety, and readability. Respond as a JSON object with keys "
    "'performance', 'safety', 'readability'. Each key maps to an object with "
    "'score' (an integer 0-100), 'summary' (a 1-2 sentence string assessment), "
    "and 'suggestions' (an array of 1-3 concrete improvement strings). "
    "Be constructive and specific. Do not include any text outside the JSON object."
)

_ANALYZE_USER_PROMPT_TEMPLATE = (
    "Analyze the following SQL statement:\n\n```sql\n{statement}\n```\n\n"
    "Return the analysis as JSON."
)


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


@router.post("/query/analyze")
async def analyze(
    body: AnalyzeRequest,
    llm_config: AppLLMConfig | None = Depends(get_llm_config),
    registry: SourceRegistry = Depends(get_registry),
) -> AnalyzeResponse:
    """Analyze a SQL statement for performance, safety, and readability.

    Uses an LLM when configured; falls back to rule-based heuristics when the
    LLM is unavailable, times out, or returns unparseable output.

    Returns 503 (``LLM_UNAVAILABLE``) when no ``[llm]`` section is configured.
    """
    if llm_config is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_UNAVAILABLE",
                "message": "LLM is not configured. Add an [llm] section to config.toml.",
            },
        )

    client: LLMClient = _build_llm_client(llm_config)
    model_name: str | None = llm_config.model

    try:
        raw_response = await client.complete(
            system=_ANALYZE_SYSTEM_PROMPT,
            user=_ANALYZE_USER_PROMPT_TEMPLATE.format(statement=body.statement),
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except LLMInvalidSQLError:
        logger.warning("LLM returned invalid response for analyze, falling back to heuristic")
        return _heuristic_analyze(body.statement)
    except Exception:
        logger.exception("Unexpected LLM error during analyze, falling back to heuristic")
        return _heuristic_analyze(body.statement)

    # Parse the JSON response; fall back to heuristic on any failure.
    try:
        parsed: dict[str, Any] = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM analyze response is not valid JSON, falling back to heuristic")
        return _heuristic_analyze(body.statement)

    def _parse_dimension(data: Any) -> AnalyzeDimension:
        if not isinstance(data, dict):
            return AnalyzeDimension(
                score=50, summary="Assessment unavailable.", suggestions=[]
            )
        return AnalyzeDimension(
            score=_clamp(int(data.get("score", 50))),
            summary=str(data.get("summary", "Assessment unavailable.")),
            suggestions=[
                str(s) for s in data.get("suggestions", []) if isinstance(s, str)
            ],
        )

    performance = _parse_dimension(parsed.get("performance"))
    safety = _parse_dimension(parsed.get("safety"))
    readability = _parse_dimension(parsed.get("readability"))
    overall = (performance.score + safety.score + readability.score) // 3

    return AnalyzeResponse(
        performance=performance,
        safety=safety,
        readability=readability,
        overall_score=overall,
        model=model_name,
    )
