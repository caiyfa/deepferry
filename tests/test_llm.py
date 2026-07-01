"""Unit tests for the LLM client (core.llm) using mocked AsyncOpenAI.

Covers generate_sql() success, error responses (empty, invalid JSON,
missing sql field), timeout, connection errors, API errors, and
health_check().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from deepferry.core.errors import (
    LLMInvalidSQLError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from deepferry.core.llm import (
    GenerateSQLRequest,
    GenerateSQLResponse,
    LLMConfig,
    OpenAICompatibleClient,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config() -> LLMConfig:
    return LLMConfig(
        provider="test-provider",
        api_key="test-key",
        model="test-model",
        base_url="https://api.test.example/v1",
        max_tokens=1024,
        temperature=0.1,
        timeout=15,
    )


@pytest.fixture
def request_data() -> GenerateSQLRequest:
    return GenerateSQLRequest(
        question="Show all customers",
        schema_context="Table: customers (id INT, name VARCHAR)",
        source_ids=["test-db"],
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_completion_mock(
    content: str,
    model: str = "test-model",
    total_tokens: int = 42,
) -> MagicMock:
    """Create a MagicMock that looks like an OpenAI chat completion."""
    completion = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    completion.choices = [choice]
    completion.model = model
    usage = MagicMock()
    usage.total_tokens = total_tokens
    completion.usage = usage
    return completion


def _patch_client(config: LLMConfig) -> tuple[
    OpenAICompatibleClient, MagicMock, AsyncMock, AsyncMock
]:
    """Patch AsyncOpenAI and return (client, mock_class, mock_create, mock_list).

    ``mock_create`` is the AsyncMock for ``chat.completions.create``.
    ``mock_list`` is the AsyncMock for ``models.list``.
    """
    patcher = patch("deepferry.core.llm.AsyncOpenAI", autospec=False)
    mock_async_openai = patcher.start()
    mock_client_instance = mock_async_openai.return_value

    mock_create = AsyncMock()
    mock_client_instance.chat.completions.create = mock_create

    mock_list = AsyncMock()
    mock_client_instance.models.list = mock_list

    client = OpenAICompatibleClient(config)

    return client, mock_async_openai, mock_create, mock_list


# ── generate_sql() — success ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_success(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql returns a valid GenerateSQLResponse on successful LLM call."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(
        content='{"sql": "SELECT * FROM customers LIMIT 10", '
        '"explanation": "Retrieves all customers with a limit of 10 rows"}',
        model="test-model",
        total_tokens=150,
    )

    response = await client.generate_sql(request_data)

    assert isinstance(response, GenerateSQLResponse)
    assert response.sql == "SELECT * FROM customers LIMIT 10"
    assert "Retrieves all customers" in response.explanation
    assert response.model == "test-model"
    assert response.tokens_used == 150

    mock_create.assert_awaited_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    assert call_kwargs["max_tokens"] == 1024
    assert call_kwargs["temperature"] == 0.1
    assert len(call_kwargs["messages"]) == 2


@pytest.mark.asyncio
async def test_generate_sql_fallback_model(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql uses config.model when completion.model is None."""
    client, _, mock_create, _ = _patch_client(config)
    completion = _make_completion_mock(
        content='{"sql": "SELECT 1", "explanation": "Returns one row"}',
        model="test-model",
    )
    completion.model = None
    mock_create.return_value = completion

    response = await client.generate_sql(request_data)

    assert response.model == "test-model"


@pytest.mark.asyncio
async def test_generate_sql_no_usage(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql returns 0 tokens when usage is None."""
    client, _, mock_create, _ = _patch_client(config)
    completion = _make_completion_mock(
        content='{"sql": "SELECT 1", "explanation": "Simple query"}',
    )
    completion.usage = None
    mock_create.return_value = completion

    response = await client.generate_sql(request_data)

    assert response.tokens_used == 0


# ── generate_sql() — empty response ───────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_empty_response(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMInvalidSQLError when the LLM returns empty content."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(content="")

    with pytest.raises(LLMInvalidSQLError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_INVALID_SQL"
    assert "empty response" in exc_info.value.message.lower()


# ── generate_sql() — invalid JSON ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_invalid_json(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMInvalidSQLError when the LLM returns non-JSON."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(content="not json")

    with pytest.raises(LLMInvalidSQLError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_INVALID_SQL"
    assert "valid json" in exc_info.value.message.lower()


# ── generate_sql() — missing 'sql' field ──────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_missing_sql_field(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMInvalidSQLError when JSON has no 'sql' field."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(
        content='{"not_sql": "x"}'
    )

    with pytest.raises(LLMInvalidSQLError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_INVALID_SQL"
    assert "missing" in exc_info.value.message.lower()
    assert "sql" in exc_info.value.message.lower()


# ── generate_sql() — timeout ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_timeout(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMTimeoutError on APITimeoutError."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.side_effect = openai.APITimeoutError(request=None)  # type: ignore[arg-type]

    with pytest.raises(LLMTimeoutError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_TIMEOUT"


# ── generate_sql() — connection error ─────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_connection_error(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMUnavailableError on APIConnectionError."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.side_effect = openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    with pytest.raises(LLMUnavailableError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_UNAVAILABLE"


# ── generate_sql() — API error ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_sql_api_error(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMUnavailableError on APIError."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.side_effect = openai.APIError(
        message="Internal server error",
        request=None,  # type: ignore[arg-type]
        body=None,
    )

    with pytest.raises(LLMUnavailableError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_UNAVAILABLE"


# ── health_check() — success ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_success(config: LLMConfig) -> None:
    """health_check returns True when models.list() succeeds."""
    client, _, _, mock_list = _patch_client(config)
    mock_list.return_value = MagicMock()

    result = await client.health_check()

    assert result is True
    mock_list.assert_awaited_once()


# ── health_check() — failure ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_failure(config: LLMConfig) -> None:
    """health_check returns False when models.list() raises an error."""
    client, _, _, mock_list = _patch_client(config)
    mock_list.side_effect = openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    result = await client.health_check()

    assert result is False


@pytest.mark.asyncio
async def test_health_check_failure_os_error(config: LLMConfig) -> None:
    """health_check returns False on OSError."""
    client, _, _, mock_list = _patch_client(config)
    mock_list.side_effect = OSError("Connection refused")

    result = await client.health_check()

    assert result is False


# ── _parse_llm_response — edge cases ──────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_llm_response_with_extra_fields(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """parse_llm_response ignores extra JSON fields beyond sql/explanation."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(
        content='{"sql": "SELECT 1", "explanation": "test", '
        '"confidence": 0.95, "extra": "ignored"}'
    )

    response = await client.generate_sql(request_data)

    assert response.sql == "SELECT 1"
    assert response.explanation == "test"


@pytest.mark.asyncio
async def test_generate_sql_whitespace_only_response(
    config: LLMConfig, request_data: GenerateSQLRequest
) -> None:
    """generate_sql raises LLMInvalidSQLError for whitespace-only content."""
    client, _, mock_create, _ = _patch_client(config)
    mock_create.return_value = _make_completion_mock(content="   \n\t  ")

    with pytest.raises(LLMInvalidSQLError) as exc_info:
        await client.generate_sql(request_data)

    assert exc_info.value.code == "LLM_INVALID_SQL"
    assert "empty" in exc_info.value.message.lower()


def test_parse_llm_response_valid() -> None:
    """_parse_llm_response extracts sql and explanation from valid JSON."""
    sql, explanation = OpenAICompatibleClient._parse_llm_response(
        '{"sql": "SELECT 1", "explanation": "Returns one row"}'
    )
    assert sql == "SELECT 1"
    assert explanation == "Returns one row"


def test_parse_llm_response_empty_string() -> None:
    """_parse_llm_response raises LLMInvalidSQLError for empty string."""
    with pytest.raises(LLMInvalidSQLError, match="empty"):
        OpenAICompatibleClient._parse_llm_response("")


def test_parse_llm_response_invalid_json() -> None:
    """_parse_llm_response raises LLMInvalidSQLError for non-JSON."""
    with pytest.raises(LLMInvalidSQLError, match="valid JSON"):
        OpenAICompatibleClient._parse_llm_response("not json")


def test_parse_llm_response_missing_sql() -> None:
    """_parse_llm_response raises LLMInvalidSQLError when 'sql' is missing."""
    with pytest.raises(LLMInvalidSQLError, match="missing"):
        OpenAICompatibleClient._parse_llm_response('{"not_sql": "x"}')
