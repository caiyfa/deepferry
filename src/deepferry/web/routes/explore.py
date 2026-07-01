"""Explore API — natural-language-to-SQL with SSE streaming and suggestions.

``POST /api/explore`` accepts a natural-language question plus source selections,
streams progress events via Server-Sent Events (SSE), and returns the query
result on completion.  ``GET /api/explore/suggestions`` returns contextual
question suggestions — LLM-powered when available, template-based otherwise.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from deepferry.config import (
    LLMConfig as AppLLMConfig,  # noqa: TC001  # FastAPI resolves type hints at runtime
)
from deepferry.core.errors import (
    DataSourceError,
    LLMInvalidSQLError,
    LLMTimeoutError,
    LLMUnavailableError,
    SourceNotFoundError,
)
from deepferry.core.llm import (
    GenerateSQLRequest,
    LLMClient,
    OpenAICompatibleClient,
)
from deepferry.core.llm import (
    LLMConfig as CoreLLMConfig,
)
from deepferry.core.llm_guard import scan_sql
from deepferry.core.models import QueryRequest, StructuredResult
from deepferry.core.prompts import PromptLoader
from deepferry.datasources.registry import (
    SourceRegistry,  # noqa: TC001  # FastAPI resolves type hints at runtime
)
from deepferry.engine import DuckDBEngine
from deepferry.web.app import get_llm_config, get_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/explore")


# ── Pydantic models ──────────────────────────────────────────────────────


class ExploreRequest(BaseModel):
    """Request body for ``POST /api/explore`` (SSE streaming endpoint)."""

    question: str = Field(..., min_length=1, description="Natural-language question")
    source_ids: list[str] = Field(
        ..., min_length=1, description="Source IDs to query against"
    )
    conversation_history: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional previous Q&A pairs for conversational context",
    )


# ── SSE helpers ──────────────────────────────────────────────────────────


def _sse_event(step: str, payload: dict[str, Any] | str) -> str:
    """Format a single SSE event as ``data: {...}\\n\\n``.

    Args:
        step: Event step name (``identifying_sources``, ``generating_sql``, etc.).
        payload: Either a string message or a dict of fields to include.
    """
    if isinstance(payload, str):
        data: dict[str, Any] = {"step": step, "message": payload}
    else:
        data = {"step": step, **payload}
    return f"data: {json.dumps(data, default=str)}\n\n"


def _sse_error(code: str, message: str, extra: dict[str, Any] | None = None) -> str:
    """Format an SSE error event.

    Args:
        code: Machine-readable error code.
        message: Human-readable error message.
        extra: Additional fields to include in the event payload.
    """
    data: dict[str, Any] = {"step": "error", "code": code, "message": message}
    if extra:
        data.update(extra)
    return f"data: {json.dumps(data, default=str)}\n\n"


# ── Schema context builder ───────────────────────────────────────────────


async def _build_schema_data(
    source_ids: list[str],
    registry: SourceRegistry,
) -> list[dict[str, Any]]:
    """Collect schema metadata from each source for prompt context.

    Calls ``schema_info()`` on every source.  Returns a list of descriptors
    in the format expected by ``PromptLoader.render_schema_context()``.

    Args:
        source_ids: List of source IDs to introspect.
        registry: The live source registry.

    Returns:
        List of source schema descriptors.

    Raises:
        SourceNotFoundError: When a requested source is not registered.
    """
    result: list[dict[str, Any]] = []
    for sid in source_ids:
        source = registry.get(sid)
        schema = await source.schema_info()
        tables: list[dict[str, Any]] = []
        for resource in schema.resources:
            tables.append(
                {
                    "name": resource.name,
                    "columns": [
                        {"name": col.name, "type": col.type}
                        for col in resource.columns
                    ],
                }
            )
        result.append(
            {
                "source_id": sid,
                "type": source.source_type,
                "tables": tables,
            }
        )
    return result


# ── LLM client factory ───────────────────────────────────────────────────


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


# ── SSE stream generator ─────────────────────────────────────────────────


async def _explore_stream(
    question: str,
    source_ids: list[str],
    conversation_history: list[dict[str, Any]] | None,
    registry: SourceRegistry,
    llm_config: AppLLMConfig,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE events for the Explore pipeline.

    Pipeline steps:
    1. ``identifying_sources`` — validate sources and collect schema metadata.
    2. ``generating_sql``    — call LLM to convert question → SQL.
    3. ``validating_sql``    — run safety scan on the generated SQL.
    4. ``executing_query``   — execute against DuckDB (multi-source) or direct.
    5. ``done``              — emit the final ``StructuredResult`` + metadata.

    Any error aborts the pipeline and emits an ``error`` event.
    """

    # ── Step 1: identifying_sources ───────────────────────────────────
    yield _sse_event(
        "identifying_sources",
        "Validating data sources and collecting schema information...",
    )

    schema_data: list[dict[str, Any]]
    try:
        schema_data = await _build_schema_data(source_ids, registry)
    except SourceNotFoundError as e:
        yield _sse_error(e.code, e.message)
        return
    except Exception as exc:
        logger.exception("Schema collection failed for sources %s", source_ids)
        yield _sse_error("SCHEMA_FAILED", str(exc))
        return

    if not schema_data:
        yield _sse_error(
            "NO_SCHEMA",
            "No schema information could be collected from the selected sources.",
        )
        return

    # Build schema context with PromptLoader
    prompt_loader = PromptLoader()
    try:
        schema_context = prompt_loader.render_schema_context(schema_data)
    except Exception as exc:
        logger.exception("Prompt rendering failed")
        yield _sse_error("PROMPT_FAILED", str(exc))
        return

    # ── Step 2: generating_sql ────────────────────────────────────────
    yield _sse_event(
        "generating_sql",
        "Generating SQL from natural language question...",
    )

    client: LLMClient = _build_llm_client(llm_config)

    if TYPE_CHECKING:
        from openai.types.chat import ChatCompletionMessageParam

    try:
        llm_request = GenerateSQLRequest(
            question=question,
            schema_context=schema_context,
            source_ids=source_ids,
            conversation_history=cast(
                "list[ChatCompletionMessageParam] | None", conversation_history
            ),
        )
        llm_response = await client.generate_sql(llm_request)
    except LLMTimeoutError as e:
        yield _sse_error(e.code, e.message)
        return
    except LLMUnavailableError as e:
        yield _sse_error(e.code, e.message)
        return
    except LLMInvalidSQLError as e:
        yield _sse_error(
            e.code,
            e.message,
            extra={"attempted_sql": getattr(e, "message", "")},
        )
        return
    except Exception as exc:
        # Catch-all: unexpected provider failures (network errors, auth
        # rejections, malformed responses) are surfaced as LLM_UNAVAILABLE so
        # the client always receives a structured error event.
        logger.exception("Unexpected LLM failure during SQL generation")
        yield _sse_error("LLM_UNAVAILABLE", str(exc))
        return

    # ── Step 3: validating_sql ────────────────────────────────────────
    yield _sse_event(
        "validating_sql",
        "Validating generated SQL for safety...",
    )

    try:
        scan_sql(llm_response.sql)
    except DataSourceError as e:
        yield _sse_error(
            e.code,
            e.message,
            extra={"sql": llm_response.sql},
        )
        return

    # ── Step 4: executing_query ───────────────────────────────────────
    yield _sse_event(
        "executing_query",
        "Executing query against data sources...",
    )

    result: StructuredResult
    try:
        if len(source_ids) >= 2:
            # Cross-source → DuckDB federation
            engine = DuckDBEngine(registry)
            await engine.connect()
            try:
                query = QueryRequest(
                    source_id="__cross__",
                    statement=llm_response.sql,
                )
                result = await engine.execute(query, registry)
            finally:
                await engine.disconnect()
        else:
            # Single source → direct datasource execution
            source = registry.get(source_ids[0])
            query = QueryRequest(
                source_id=source_ids[0],
                statement=llm_response.sql,
            )
            result = await source.execute(query)
    except Exception as exc:
        logger.exception("Query execution failed for sources %s", source_ids)
        if isinstance(exc, DataSourceError):
            yield _sse_error(exc.code, exc.message)
        else:
            yield _sse_error("EXECUTION_FAILED", str(exc))
        return

    # ── Step 5: done ──────────────────────────────────────────────────
    yield _sse_event(
        "done",
        {
            "result": result.model_dump(mode="json"),
            "explanation": llm_response.explanation,
            "model": llm_response.model,
            "tokens_used": llm_response.tokens_used,
        },
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("")
async def explore(
    body: ExploreRequest,
    registry: SourceRegistry = Depends(get_registry),
    llm_config: AppLLMConfig | None = Depends(get_llm_config),
) -> StreamingResponse:
    """Convert a natural-language question to SQL and execute it, streaming progress via SSE.

    Returns a ``text/event-stream`` response that emits the following events
    in order: ``identifying_sources``, ``generating_sql``, ``validating_sql``,
    ``executing_query``, ``done``.  Errors abort the stream with an ``error``
    event.

    Requires an ``[llm]`` section in ``config.toml``.  Returns 503 when LLM
    is not configured.
    """
    if llm_config is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_UNAVAILABLE",
                "message": "LLM is not configured. Add an [llm] section to config.toml "
                "with provider, api_key, model, and base_url fields.",
            },
        )

    return StreamingResponse(
        _explore_stream(
            question=body.question,
            source_ids=body.source_ids,
            conversation_history=body.conversation_history,
            registry=registry,
            llm_config=llm_config,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/suggestions")
async def suggestions(
    source_ids: str = Query(
        ...,
        description="Comma-separated source IDs (e.g. 'mysql-main,pg-analytics')",
    ),
    registry: SourceRegistry = Depends(get_registry),
    llm_config: AppLLMConfig | None = Depends(get_llm_config),
) -> dict[str, list[str]]:
    """Return contextual question suggestions for the selected sources.

    When LLM is configured, uses the language model to generate questions
    tailored to the actual schema.  Falls back to template-based suggestions
    when the LLM is unavailable or the call fails.
    """
    ids = [s.strip() for s in source_ids.split(",") if s.strip()]
    if not ids:
        return {"suggestions": []}

    # Try LLM-based suggestions first
    if llm_config is not None:
        try:
            llm_suggestions = await _generate_llm_suggestions(ids, registry, llm_config)
            if llm_suggestions and len(llm_suggestions) >= 3:
                return {"suggestions": llm_suggestions[:5]}
        except Exception:
            logger.warning(
                "LLM-based suggestions failed, falling back to templates",
                exc_info=True,
            )

    # Template-based fallback
    template_suggestions = await _generate_template_suggestions(ids, registry)
    return {"suggestions": template_suggestions[:5]}


# ── Suggestion generators ────────────────────────────────────────────────


async def _generate_llm_suggestions(
    source_ids: list[str],
    registry: SourceRegistry,
    llm_config: AppLLMConfig,
) -> list[str]:
    """Use the LLM to generate contextual question suggestions.

    Builds a brief schema summary and asks the LLM to propose diverse,
    natural-language questions a user might ask about the data.
    """
    # Collect a lightweight schema summary
    schema_data: list[dict[str, Any]] = []
    for sid in source_ids:
        try:
            source = registry.get(sid)
            schema = await source.schema_info()
            tables: list[dict[str, Any]] = []
            for r in schema.resources:
                tables.append(
                    {
                        "name": r.name,
                        "columns": [
                            {"name": c.name, "type": c.type} for c in r.columns
                        ],
                    }
                )
            schema_data.append(
                {"source_id": sid, "type": source.source_type, "tables": tables}
            )
        except Exception:
            logger.warning("Skipping source %r for suggestion generation", sid)
            continue

    if not schema_data:
        return []

    prompt_loader = PromptLoader()
    schema_context = prompt_loader.render_schema_context(schema_data)

    # Build a lightweight chat completion for suggestions
    from openai import (
        APIConnectionError,
        APIError,
        APITimeoutError,
        AsyncOpenAI,
    )

    async_client = AsyncOpenAI(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        timeout=float(llm_config.timeout),
    )

    system_msg = (
        "You are a helpful assistant that generates natural-language questions "
        "for database exploration. Given a database schema, propose questions "
        "a business user might ask. Return your response as JSON with a "
        '"questions" key containing an array of strings.'
    )
    user_msg = (
        f"Database schema:\n{schema_context}\n\n"
        "Generate 5 diverse natural-language questions a user might ask "
        'about this data. Return JSON: {"questions": ["question 1", ...]}. '
        "Make questions specific to the schema shown — reference actual table "
        "and column names."
    )

    try:
        completion = await async_client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
    except APITimeoutError:
        logger.warning("LLM suggestion generation timed out")
        return []
    except (APIConnectionError, APIError):
        logger.warning("LLM suggestion generation failed — API error")
        return []

    content = completion.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
        questions = parsed.get("questions", [])
    except json.JSONDecodeError:
        logger.warning("LLM suggestion response was not valid JSON")
        return []

    return [str(q) for q in questions if isinstance(q, str)][:5]


async def _generate_template_suggestions(
    source_ids: list[str],
    registry: SourceRegistry,
) -> list[str]:
    """Generate template-based question suggestions from schema metadata.

    Walks through each source's tables and columns, producing standard
    exploratory questions like "Show me all <table>" and "How many <table>
    are there?".
    """
    suggestions: list[str] = []
    seen: set[str] = set()

    for sid in source_ids:
        try:
            source = registry.get(sid)
            schema = await source.schema_info()
        except Exception:
            logger.warning("Skipping source %r for template suggestions", sid)
            continue

        for resource in schema.resources:
            table = resource.name
            columns = resource.columns

            # Basic table overview
            q = f"Show me all records from {table}"
            if q not in seen:
                suggestions.append(q)
                seen.add(q)

            # Row count
            q = f"How many records are in {table}?"
            if q not in seen:
                suggestions.append(q)
                seen.add(q)

            # Column-specific suggestions (up to 3 columns per table)
            for col in columns[:3]:
                q = f"What are the distinct values of {col.name} in {table}?"
                if q not in seen:
                    suggestions.append(q)
                    seen.add(q)

            if len(suggestions) >= 15:
                break

        if len(suggestions) >= 15:
            break

    # Guarantee at least 3 suggestions when tables exist
    if len(suggestions) < 3 and suggestions:
        # Pad with generic questions if needed
        generic = [
            f"Show me the first 10 rows from {suggestions[0].split()[-1]}" if suggestions else "Show me sample data",
            "What tables are available?",
            "Show me the database schema",
        ]
        for g in generic:
            if g not in seen and len(suggestions) < 3:
                suggestions.append(g)
                seen.add(g)

    return suggestions[:10]
