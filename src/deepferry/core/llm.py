"""Pluggable LLM client for natural-language to SQL generation.

Provides a clean abstraction over OpenAI-compatible APIs (DeepSeek, OpenAI,
Ollama, and local models).  The frontend never touches API keys — the LLM
client is a backend-only concern invoked by Explore mode and AI optimization
features.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI
from openai.types.shared_params.response_format_json_object import (
    ResponseFormatJSONObject,
)

from deepferry.core.errors import (
    LLMInvalidSQLError,
    LLMTimeoutError,
    LLMUnavailableError,
)

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

logger = logging.getLogger(__name__)

# ── System prompt (static safety rules) ─────────────────────────────────

_SYSTEM_PROMPT: str = (
    "You are an expert SQL query generator. Your task is to convert natural language "
    "questions into safe, correct SQL statements.\n\n"
    "Safety rules you MUST follow:\n"
    "- Generate ONLY SELECT queries. Never produce INSERT, UPDATE, DELETE, DROP, "
    "ALTER, TRUNCATE, or any data-modifying statements.\n"
    "- Always include a LIMIT clause. Default to LIMIT 100 if the user does not "
    "specify one.\n"
    "- Use fully-qualified column names (table.column) when joining multiple tables.\n"
    "- Prefer standard SQL syntax compatible with MySQL and PostgreSQL.\n\n"
    "Respond in JSON format with exactly two fields:\n"
    '{"sql": "<the generated SQL statement>", "explanation": "<brief explanation '
    'of what the query does and any assumptions made>"}'
)

# ── Data structures ─────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """Configuration for an LLM provider.

    All fields can be populated from a ``[llm]`` section in ``config.toml``.
    ``api_key`` must use ``${ENV_VAR}`` syntax — secrets are never stored
    in committed config files.
    """

    provider: str
    api_key: str
    model: str
    base_url: str
    max_tokens: int = 2000
    temperature: float = 0.1
    timeout: int = 15


@dataclass
class GenerateSQLRequest:
    """Input for NL-to-SQL generation.

    ``schema_context`` must be a pre-rendered string containing table names,
    columns, types, row counts, and sample values.  Prompt template assembly
    happens in the prompts module, not here.
    """

    question: str
    schema_context: str
    source_ids: list[str] = field(default_factory=list)
    conversation_history: list[ChatCompletionMessageParam] | None = None


@dataclass
class GenerateSQLResponse:
    """Result of an NL-to-SQL generation call."""

    sql: str
    explanation: str
    model: str
    tokens_used: int


# ── Abstract interface ─────────────────────────────────────────────────


class LLMClient(ABC):
    """Abstract interface for LLM providers.

    Subclasses implement ``generate_sql()`` and ``health_check()`` for a
    specific API format.  Adding a new provider requires no changes to
    Explore mode or AI optimization code.
    """

    @abstractmethod
    async def generate_sql(self, request: GenerateSQLRequest) -> GenerateSQLResponse:
        """Convert a natural-language question into a SQL statement.

        Parameters
        ----------
        request : GenerateSQLRequest
            Contains the user question, schema context, and optional
            conversation history.

        Returns
        -------
        GenerateSQLResponse
            The generated SQL, explanation, model used, and token count.

        Raises
        ------
        LLMUnavailableError
            When the LLM API is unreachable or returns an unexpected error.
        LLMTimeoutError
            When the API call exceeds the configured timeout.
        LLMInvalidSQLError
            When the LLM response cannot be parsed as valid JSON with
            ``sql`` and ``explanation`` fields.
        """
        ...

    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """Send a generic chat completion and return the raw text response.

        Parameters
        ----------
        system : str
            The system message (instructions for the assistant).
        user : str
            The user message (the task or question).

        Returns
        -------
        str
            The raw text content from the LLM response.

        Raises
        ------
        LLMUnavailableError
            When the LLM API is unreachable or returns an unexpected error.
        LLMTimeoutError
            When the API call exceeds the configured timeout.
        LLMInvalidSQLError
            When the LLM response is empty or cannot be processed.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify connectivity to the LLM provider.

        Returns
        -------
        bool
            ``True`` if the API is reachable and responding, ``False`` otherwise.
        """
        ...


# ── OpenAI-compatible implementation ────────────────────────────────────


class OpenAICompatibleClient(LLMClient):
    """LLM client backed by any OpenAI-compatible API.

    Works with OpenAI, DeepSeek (``api.deepseek.com/v1``), Ollama
    (``localhost:11434/v1``), and custom proxies.  The ``base_url``
    parameter in ``LLMConfig`` determines the target endpoint.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=float(config.timeout),
        )

    # ── LLMClient interface ─────────────────────────────────────────────

    async def generate_sql(self, request: GenerateSQLRequest) -> GenerateSQLResponse:
        """Send a chat completion request and parse the response.

        The prompt consists of: system message (SQL expert + safety rules),
        schema context, then the user question.
        """
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]

        # Inject conversation history before the current question.
        if request.conversation_history:
            messages.extend(request.conversation_history)

        # Schema context and user question are combined in one user message
        # so the LLM has full context for a single-response generation.
        user_content = request.question
        if request.schema_context:
            user_content = (
                f"Database schema:\n{request.schema_context}\n\n"
                f"Question: {request.question}"
            )
        messages.append({"role": "user", "content": user_content})

        try:
            completion = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                response_format=ResponseFormatJSONObject(type="json_object"),
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                message=f"LLM call to {self._config.provider} timed out after "
                f"{self._config.timeout}s",
                suggestion="Increase the timeout in config.toml or check if the "
                "LLM service is overloaded",
            ) from exc
        except APIConnectionError as exc:
            raise LLMUnavailableError(
                message=f"Cannot reach {self._config.provider} at "
                f"{self._config.base_url}",
                suggestion="Verify the LLM service is running and the base_url is correct",
            ) from exc
        except APIError as exc:
            raise LLMUnavailableError(
                message=f"{self._config.provider} API error: {exc}",
                suggestion="Check your API key and account status",
            ) from exc

        raw_content: str = completion.choices[0].message.content or ""
        model: str = completion.model or self._config.model
        tokens_used: int = completion.usage.total_tokens if completion.usage else 0

        sql, explanation = self._parse_llm_response(raw_content)

        logger.info(
            "LLM generated SQL — model=%s tokens=%d latency_ms=%.0f",
            model,
            tokens_used,
            0,  # latency tracked by caller via TraceSink; placeholder here
        )

        return GenerateSQLResponse(
            sql=sql,
            explanation=explanation,
            model=model,
            tokens_used=tokens_used,
        )

    async def complete(self, system: str, user: str) -> str:
        """Send a generic chat completion and return the raw text content.

        Uses JSON object response format for structured output.
        """

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        try:
            completion = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                response_format=ResponseFormatJSONObject(type="json_object"),
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                message=f"LLM call to {self._config.provider} timed out after "
                f"{self._config.timeout}s",
                suggestion="Increase the timeout in config.toml or check if the "
                "LLM service is overloaded",
            ) from exc
        except APIConnectionError as exc:
            raise LLMUnavailableError(
                message=f"Cannot reach {self._config.provider} at "
                f"{self._config.base_url}",
                suggestion="Verify the LLM service is running and the base_url is correct",
            ) from exc
        except APIError as exc:
            raise LLMUnavailableError(
                message=f"{self._config.provider} API error: {exc}",
                suggestion="Check your API key and account status",
            ) from exc

        return completion.choices[0].message.content or ""

    async def health_check(self) -> bool:
        """Send a minimal API call to verify connectivity."""
        try:
            await self._client.models.list()
            return True
        except (APIError, APIConnectionError, APITimeoutError, OSError):
            logger.warning(
                "LLM health check failed for provider=%s at %s",
                self._config.provider,
                self._config.base_url,
                exc_info=True,
            )
            return False

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _parse_llm_response(raw_content: str) -> tuple[str, str]:
        """Extract ``sql`` and ``explanation`` from an LLM JSON response.

        Falls back to treating the entire response as SQL if JSON parsing
        fails, with a generic explanation.
        """
        if not raw_content.strip():
            raise LLMInvalidSQLError(
                message="LLM returned an empty response",
                suggestion="The model may not support the requested task; "
                "try a different model",
            )

        try:
            parsed: dict[str, Any] = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidSQLError(
                message="LLM response is not valid JSON",
                suggestion="The model may not support JSON output; try adding "
                "'Respond in JSON format' to the system prompt",
            ) from exc

        sql = parsed.get("sql", "")
        explanation = parsed.get("explanation", "")

        if not sql:
            raise LLMInvalidSQLError(
                message="LLM response is missing the 'sql' field",
                suggestion="The model response format may have changed; "
                "review the system prompt and model capabilities",
            )

        return str(sql), str(explanation)
