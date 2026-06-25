"""Tests for OrchestrationEngine and OrchestratedDataSource.

Covers interpolation, linear flows, foreach fan-out, error handling,
cycle detection, and TraceSink integration.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock

import httpx2
import pytest

from deepferry.config import SourceConfig
from deepferry.core.errors import ConfigError, DataSourceError
from deepferry.core.models import ColumnMeta, QueryRequest, StructuredResult
from deepferry.core.trace import (
    Execution,
    Span,
    SpanKind,
    SpanStatus,
    TraceSink,
)
from deepferry.datasources.orchestrated import (
    ForeachConfig,
    OrchestratedDataSource,
    OrchestrationConfig,
    OrchestrationEngine,
    Step,
    _detect_cycles,
    _detect_undefined_bindings,
    interpolate,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _mock_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _mock_http_client(responses: list[MagicMock] | None = None) -> AsyncMock:
    client = AsyncMock()
    if responses:
        client.request = AsyncMock(side_effect=responses)
    else:
        client.request = AsyncMock(return_value=_mock_response())
    client.aclose = AsyncMock()
    client.get = AsyncMock(return_value=_mock_response())
    return client


def _mock_trace_sink() -> AsyncMock:
    ts = AsyncMock(spec=TraceSink)

    execution_id_counter = 0

    async def start_execution(source_id: str, root_query_id: int | None = None) -> Execution:
        nonlocal execution_id_counter
        execution_id_counter += 1
        return Execution(
            id=execution_id_counter,
            root_query_id=root_query_id,
            source_id=source_id,
            started_at=int(time.time() * 1000),
        )

    span_id_counter = 0

    async def add_span(execution: Execution, span: Span) -> Span:
        nonlocal span_id_counter
        span_id_counter += 1
        return Span(
            id=span_id_counter,
            execution_id=execution.id,
            parent_span_id=span.parent_span_id,
            span_kind=span.span_kind,
            span_name=span.span_name,
            source_id=span.source_id,
            started_at=int(time.time() * 1000),
            attributes=dict(span.attributes),
        )

    ts.start_execution = AsyncMock(side_effect=start_execution)
    ts.add_span = AsyncMock(side_effect=add_span)
    ts.finish_span = AsyncMock()
    ts.finish_execution = AsyncMock()
    return ts


def _make_source_config(
    *,
    source_id: str = "test-orch",
    base_url: str = "https://api.example.com",
    steps: list[dict[str, Any]] | None = None,
    foreach: dict[str, Any] | None = None,
    **extra: Any,
) -> SourceConfig:
    extras: dict[str, Any] = extra
    if steps is not None:
        extras["steps"] = steps
    if foreach is not None:
        extras["foreach"] = foreach
    return SourceConfig(
        id=source_id,
        type="orchestrated",
        base_url=base_url,
        extra=extras,
    )


# ── Interpolation tests ────────────────────────────────────────────────


def test_interpolate_simple_var():
    ctx = {"sql": "SELECT 1"}
    assert interpolate("{{sql}}", ctx) == "SELECT 1"


def test_interpolate_nested_var():
    ctx = {"auth": {"access_token": "tok_abc123"}}
    assert interpolate("Bearer {{auth.access_token}}", ctx) == "Bearer tok_abc123"


def test_interpolate_array_index():
    ctx = {"instances": [{"id": "inst-1"}, {"id": "inst-2"}]}
    assert interpolate("{{instances[0].id}}", ctx) == "inst-1"
    assert interpolate("{{instances[1].id}}", ctx) == "inst-2"


def test_interpolate_unresolved_raises():
    ctx = {"auth": {}}
    with pytest.raises(DataSourceError) as exc_info:
        interpolate("{{auth.access_token}}", ctx)
    assert exc_info.value.code == "INVALID_BINDING"


def test_interpolate_no_braces_unchanged():
    ctx: dict[str, Any] = {}
    assert interpolate("/api/users", ctx) == "/api/users"


def test_interpolate_multiple_bindings():
    ctx = {"a": "hello", "b": "world"}
    assert interpolate("{{a}} {{b}}", ctx) == "hello world"


def test_interpolate_none_value():
    ctx = {"val": None}
    assert interpolate("{{val}}", ctx) == ""


def test_interpolate_rejects_jinja_filters():
    ctx = {"x": "hi"}
    with pytest.raises(DataSourceError) as exc_info:
        interpolate("{{ x | upper }}", ctx)
    assert exc_info.value.code == "INVALID_BINDING"


def test_interpolate_missing_key_deep():
    ctx = {"auth": {"token": "t"}}
    with pytest.raises(DataSourceError) as exc_info:
        interpolate("{{auth.nonexistent}}", ctx)
    assert exc_info.value.code == "INVALID_BINDING"


# ── Cycle detection tests ──────────────────────────────────────────────


def test_cycle_detection_rejects_self_dependency():
    steps = [
        Step(name="login", url="/login", output_key="auth"),
        Step(name="query", url="/query?token={{query.token}}", output_key="result"),
    ]
    config = OrchestrationConfig(source_id="test", base_url="http://x", steps=steps)
    with pytest.raises(ConfigError) as exc_info:
        _detect_cycles(config)
    assert exc_info.value.code == "CIRCULAR_DEPENDENCY"


def test_cycle_detection_rejects_forward_dependency():
    steps = [
        Step(name="login", url="/login?t={{query.token}}", output_key="auth"),
        Step(name="query", url="/query", output_key="result"),
    ]
    config = OrchestrationConfig(source_id="test", base_url="http://x", steps=steps)
    with pytest.raises(ConfigError) as exc_info:
        _detect_cycles(config)
    assert exc_info.value.code == "CIRCULAR_DEPENDENCY"


def test_cycle_detection_allows_linear():
    steps = [
        Step(name="login", url="/login", output_key="auth"),
        Step(name="query", url="/query?token={{auth.token}}", output_key="result"),
    ]
    config = OrchestrationConfig(source_id="test", base_url="http://x", steps=steps)
    _detect_cycles(config)  # should not raise


def test_undefined_binding_raises():
    steps = [
        Step(name="login", url="/login", output_key="auth"),
        Step(name="query", url="/query?token={{nonexistent.token}}", output_key="r"),
    ]
    config = OrchestrationConfig(source_id="test", base_url="http://x", steps=steps)
    with pytest.raises(ConfigError) as exc_info:
        _detect_undefined_bindings(config)
    assert exc_info.value.code == "INVALID_BINDING"


def test_undefined_binding_allows_reserved():
    steps = [
        Step(name="login", url="/login?sql={{sql}}&p={{params}}", output_key="auth"),
        Step(name="query", url="/query?token={{auth.token}}&item={{item}}", output_key="r"),
    ]
    config = OrchestrationConfig(source_id="test", base_url="http://x", steps=steps)
    _detect_undefined_bindings(config)  # should not raise


# ── OrchestrationEngine ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linear_3_step_flow():
    """Test login → discover → query flow."""
    http = _mock_http_client([
        _mock_response(200, {"access_token": "tok_123"}),
        _mock_response(200, {"instances": [{"id": "i1", "name": "db1"}]}),
        _mock_response(200, {"data": [{"id": 1, "name": "Alice"}]}),
    ])
    trace = _mock_trace_sink()
    engine = OrchestrationEngine(http, trace)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", method="POST", url="/auth/login", output_key="auth"),
            Step(
                name="discover",
                url="/instances",
                headers={"Authorization": "Bearer {{auth.access_token}}"},
                output_key="instances",
            ),
            Step(
                name="query",
                url="/query",
                headers={"Authorization": "Bearer {{auth.access_token}}"},
                body={"sql": "{{sql}}"},
                output_key="result",
            ),
        ],
    )

    result = await engine.run(config, {"sql": "SELECT 1"})

    assert result.row_count == 1
    assert result.columns[0].name == "id"
    assert result.rows[0] == {"id": 1, "name": "Alice"}

    # Verify span lifecycle
    assert trace.start_execution.call_count == 1
    assert trace.add_span.call_count == 4  # 1 root + 3 step spans
    assert trace.finish_span.call_count == 3  # 3 step spans finished
    assert trace.finish_execution.call_count == 1


@pytest.mark.asyncio
async def test_foreach_union_mode():
    """Test foreach fan-out with union merge across 3 instances."""
    http = _mock_http_client([
        _mock_response(200, {"access_token": "tok"}),
        _mock_response(200, {"instances": [{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]}),
        _mock_response(200, {"data": [{"val": 1}]}),
        _mock_response(200, {"data": [{"val": 2}]}),
        _mock_response(200, {"data": [{"val": 3}]}),
    ])
    trace = _mock_trace_sink()
    engine = OrchestrationEngine(http, trace)

    foreach_step = Step(
        name="query_each",
        url="/instances/{{item.id}}/query",
        output_key="rows",
    )

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", method="POST", url="/auth/login", output_key="auth"),
            Step(name="discover", url="/instances", output_key="discovery"),
        ],
        foreach=ForeachConfig(
            array_binding="discovery.instances",
            item_binding="item",
            step=foreach_step,
            mode="union",
        ),
    )

    result = await engine.run(config, {"sql": "SELECT 1"})

    assert result.row_count == 3
    assert all(r["val"] in (1, 2, 3) for r in result.rows)

    # 3 foreach spans
    assert trace.add_span.call_count == 6  # 1 root + 2 linear + 3 foreach


@pytest.mark.asyncio
async def test_foreach_separate_mode():
    """Test foreach fan-out with separate mode."""
    http = _mock_http_client([
        _mock_response(200, {"access_token": "tok"}),
        _mock_response(200, {"instances": [{"id": "i1"}, {"id": "i2"}]}),
        _mock_response(200, {"rows": [{"a": 1}]}),
        _mock_response(200, {"rows": [{"b": 2}]}),
    ])
    engine = OrchestrationEngine(http)

    foreach_step = Step(
        name="query_each",
        url="/instances/{{item.id}}/query",
        output_key="rows",
    )

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/auth/login", output_key="auth"),
            Step(name="discover", url="/instances", output_key="discovery"),
        ],
        foreach=ForeachConfig(
            array_binding="discovery.instances",
            item_binding="item",
            step=foreach_step,
            mode="separate",
        ),
    )

    result = await engine.run(config, {"sql": "SELECT 1"})

    # In separate mode, returns the last element's result
    assert result.row_count == 1
    assert result.rows[0] == {"b": 2}


@pytest.mark.asyncio
async def test_foreach_schema_mismatch_raises():
    """Test that union mode rejects mismatched schemas."""
    http = _mock_http_client([
        _mock_response(200, {"access_token": "tok"}),
        _mock_response(200, {"instances": [{"id": "i1"}, {"id": "i2"}]}),
        _mock_response(200, {"data": [{"a": 1}]}),
        _mock_response(200, {"data": [{"b": 2}]}),
    ])
    engine = OrchestrationEngine(http)

    foreach_step = Step(
        name="query_each",
        url="/instances/{{item.id}}/query",
        output_key="rows",
    )

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/auth/login", output_key="auth"),
            Step(name="discover", url="/instances", output_key="discovery"),
        ],
        foreach=ForeachConfig(
            array_binding="discovery.instances",
            item_binding="item",
            step=foreach_step,
            mode="union",
        ),
    )

    with pytest.raises(DataSourceError) as exc_info:
        await engine.run(config, {"sql": "SELECT 1"})
    assert exc_info.value.code == "FOREACH_SCHEMA_MISMATCH"


@pytest.mark.asyncio
async def test_step_error_propagates():
    """Test that step HTTP errors are wrapped and propagated."""
    http = _mock_http_client([
        _mock_response(200, {"access_token": "tok"}),
        _mock_response(500, {}, "Internal Server Error"),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/auth/login", output_key="auth"),
            Step(name="query", url="/query", output_key="result"),
        ],
    )

    with pytest.raises(DataSourceError) as exc_info:
        await engine.run(config, {"sql": "SELECT 1"})
    assert exc_info.value.code == "STEP_FAILED"


@pytest.mark.asyncio
async def test_step_error_skip():
    """Test that on_error=skip continues execution."""
    http = _mock_http_client([
        _mock_response(200, {"token": "t"}),
        _mock_response(500, {}, "fail"),
        _mock_response(200, {"data": [{"ok": True}]}),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(name="optional", url="/optional", output_key="opt", on_error="skip"),
            Step(name="main", url="/main", output_key="result"),
        ],
    )

    result = await engine.run(config, {"sql": "SELECT 1"})
    assert result.rows[0]["ok"] is True


@pytest.mark.asyncio
async def test_step_error_retry():
    """Test on_error=retry retries once then succeeds."""
    http = _mock_http_client([
        _mock_response(200, {"token": "t"}),
        _mock_response(500, {}, "first fail"),
        _mock_response(200, {"data": [{"retried": True}]}),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(name="flaky", url="/flaky", output_key="result", on_error="retry"),
        ],
    )

    result = await engine.run(config, {"sql": "SELECT 1"})
    assert result.rows[0]["retried"] is True
    # 3 requests: login, flaky fail, flaky retry success
    assert http.request.call_count == 3


@pytest.mark.asyncio
async def test_variable_binding_across_steps():
    """Test that step outputs are available to downstream steps."""
    http = _mock_http_client([
        _mock_response(200, {"token": "abc", "user": "admin"}),
        _mock_response(200, {"data": [{"status": "ok"}]}),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(
                name="query",
                url="/query?t={{auth.token}}&u={{auth.user}}",
                output_key="result",
            ),
        ],
    )

    result = await engine.run(config, {"sql": "SELECT 1"})
    assert result.rows[0]["status"] == "ok"

    # Verify the URL was interpolated
    call_args = http.request.call_args_list[1]
    assert "/query?t=abc&u=admin" in str(call_args)


@pytest.mark.asyncio
async def test_relative_url_resolution():
    """Test that relative URLs are resolved against base_url."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"x": 1}]}),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com/v1",
        steps=[
            Step(name="query", url="/data", output_key="result"),
        ],
    )

    await engine.run(config, {"sql": "SELECT 1"})
    call = http.request.call_args_list[0]
    assert call.kwargs["url"] == "https://api.example.com/v1/data"


@pytest.mark.asyncio
async def test_trace_sink_span_attributes():
    """Test that span attributes include step metadata."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"x": 1}]}),
    ])
    trace = _mock_trace_sink()
    engine = OrchestrationEngine(http, trace)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="query", url="/data", output_key="result"),
        ],
    )

    await engine.run(config, {"sql": "SELECT 1"})

    # Check root span was created
    root_call = trace.add_span.call_args_list[0]
    assert root_call[0][1].span_kind == SpanKind.orchestration
    assert root_call[0][1].span_name == "orchestrate:test"

    # Check step span was created with attributes
    step_call = trace.add_span.call_args_list[1]
    step_span = step_call[0][1]
    assert step_span.span_kind == SpanKind.http_request
    assert step_span.span_name == "query"
    assert step_span.attributes.get("method") == "GET"


# ── OrchestratedDataSource ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_runs_engine():
    """Test that OrchestratedDataSource.execute() delegates to the engine."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"name": "test"}]}),
    ])
    config = _make_source_config(
        steps=[{"name": "query", "url": "/data", "output_key": "result"}],
    )

    source = OrchestratedDataSource(config, http_client=http)
    await source.connect()

    result = await source.execute(QueryRequest(
        source_id="test-orch",
        statement="SELECT 1",
    ))

    assert result.row_count == 1
    assert result.rows[0]["name"] == "test"


@pytest.mark.asyncio
async def test_health_check_ok():
    """Test health_check returns ok for reachable base URL."""
    http = _mock_http_client()
    http.get = AsyncMock(return_value=_mock_response(200))
    config = SourceConfig(
        id="test-orch",
        type="orchestrated",
        base_url="https://api.example.com",
        extra={"steps": [{"name": "q", "url": "/q", "output_key": "r"}]},
    )

    source = OrchestratedDataSource(config, http_client=http)
    health = await source.health_check()
    assert health.ok is True


@pytest.mark.asyncio
async def test_health_check_unreachable():
    """Test health_check returns not ok for unreachable base URL."""
    config = SourceConfig(
        id="test-orch",
        type="orchestrated",
        base_url="https://no-such-host.invalid",
        extra={"steps": [{"name": "q", "url": "/q", "output_key": "r"}]},
    )

    source = OrchestratedDataSource(config)
    health = await source.health_check()
    assert health.ok is False
    assert health.error is not None


@pytest.mark.asyncio
async def test_cycle_detection_at_construction():
    """Test that cycle detection fires at construction time (not query time)."""
    config = _make_source_config(
        steps=[
            {"name": "a", "url": "/a?t={{a.x}}", "output_key": "x"},
        ],
    )
    with pytest.raises(ConfigError) as exc_info:
        OrchestratedDataSource(config)
    assert exc_info.value.code == "CIRCULAR_DEPENDENCY"


@pytest.mark.asyncio
async def test_undefined_binding_at_construction():
    """Test that undefined bindings are caught at construction time."""
    config = _make_source_config(
        steps=[
            {"name": "a", "url": "/a?t={{nonexistent.x}}", "output_key": "x"},
        ],
    )
    with pytest.raises(ConfigError) as exc_info:
        OrchestratedDataSource(config)
    assert exc_info.value.code == "INVALID_BINDING"


@pytest.mark.asyncio
async def test_no_steps_raises():
    """Test that missing steps raise ConfigError."""
    config = SourceConfig(
        id="test-orch",
        type="orchestrated",
        base_url="https://api.example.com",
        extra={},
    )
    with pytest.raises(ConfigError) as exc_info:
        OrchestratedDataSource(config)
    assert exc_info.value.code == "MISSING_FIELD"


@pytest.mark.asyncio
async def test_list_resources_from_config():
    """Test that static resources from config are returned."""
    http = _mock_http_client()
    config = _make_source_config(
        steps=[{"name": "q", "url": "/q", "output_key": "r"}],
        resources=[
            {"name": "users", "type": "endpoint", "description": "User list"},
        ],
    )
    source = OrchestratedDataSource(config, http_client=http)
    await source.connect()

    resources = await source.list_resources()
    assert len(resources) == 1
    assert resources[0].name == "users"


@pytest.mark.asyncio
async def test_connect_disconnect_idempotent():
    """Test that connect and disconnect are safe to call multiple times."""
    http = _mock_http_client()
    config = _make_source_config(
        steps=[{"name": "q", "url": "/q", "output_key": "r"}],
    )

    source = OrchestratedDataSource(config, http_client=http)
    await source.connect()
    await source.connect()  # should be no-op
    await source.disconnect()
    await source.disconnect()  # should be no-op


@pytest.mark.asyncio
async def test_execute_passes_params():
    """Test that query.params are bound as {{params}} in step templates."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"ok": True}]}),
    ])
    config = _make_source_config(
        steps=[{"name": "query", "url": "/data", "output_key": "result"}],
    )
    source = OrchestratedDataSource(config, http_client=http)
    await source.connect()

    result = await source.execute(QueryRequest(
        source_id="test-orch",
        statement="SELECT * FROM t WHERE x = ?",
        params={"x": 42},
    ))

    assert result.row_count == 1


@pytest.mark.asyncio
async def test_run_emits_root_and_step_spans():
    """Test that the engine emits the correct span hierarchy."""
    http = _mock_http_client([
        _mock_response(200, {"token": "t"}),
        _mock_response(200, {"data": [{"x": 1}]}),
    ])
    trace = _mock_trace_sink()
    engine = OrchestrationEngine(http, trace)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(name="query", url="/query", output_key="result"),
        ],
    )

    await engine.run(config, {"sql": "SELECT 1"})

    assert trace.start_execution.call_count == 1
    # 3 spans: 1 root + 2 step spans
    assert trace.add_span.call_count == 3
    assert trace.finish_span.call_count == 2  # step spans only
    assert trace.finish_execution.call_count == 1

    # Verify span kinds
    spans = [call[0][1] for call in trace.add_span.call_args_list]
    span_kinds = [s.span_kind for s in spans]
    assert SpanKind.orchestration in span_kinds
    assert span_kinds.count(SpanKind.http_request) == 2


@pytest.mark.asyncio
async def test_run_error_marks_execution_error():
    """Test that execution is marked as error when a step fails."""
    http = _mock_http_client([
        _mock_response(200, {"token": "t"}),
        _mock_response(500, {}, "fail"),
    ])
    trace = _mock_trace_sink()
    engine = OrchestrationEngine(http, trace)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(name="query", url="/query", output_key="result"),
        ],
    )

    with pytest.raises(DataSourceError):
        await engine.run(config, {"sql": "SELECT 1"})

    assert trace.finish_execution.call_count == 1
    assert trace.finish_execution.call_args[0][1] == SpanStatus.error


# ── Interpolate dict tests ─────────────────────────────────────────────


def test_interpolate_dict():
    from deepferry.datasources.orchestrated import interpolate_dict

    ctx = {"t": "tok", "v": "42"}
    tmpl = {"auth": "Bearer {{t}}", "body": {"val": "{{v}}"}}
    result = interpolate_dict(tmpl, ctx)

    assert result == {"auth": "Bearer tok", "body": {"val": "42"}}  # type: ignore[index]


def test_interpolate_dict_none():
    from deepferry.datasources.orchestrated import interpolate_dict

    assert interpolate_dict(None, {}) is None


# ── Edge cases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_foreach_array():
    """Test foreach over an empty array."""
    http = _mock_http_client([
        _mock_response(200, {"token": "t"}),
        _mock_response(200, {"instances": []}),
    ])
    engine = OrchestrationEngine(http)

    foreach_step = Step(name="q", url="/q/{{item.id}}", output_key="r")
    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="login", url="/login", output_key="auth"),
            Step(name="discover", url="/discover", output_key="discovery"),
        ],
        foreach=ForeachConfig(
            array_binding="discovery.instances",
            item_binding="item",
            step=foreach_step,
            mode="union",
        ),
    )

    result = await engine.run(config, {"sql": "SELECT 1"})
    assert result.row_count == 0
    assert result.rows == []


@pytest.mark.asyncio
async def test_step_without_output_key_uses_name():
    """Test that step output is stored under step name when output_key is None."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"x": 1}]}),
        _mock_response(200, {"data": [{"y": 2}]}),
    ])
    engine = OrchestrationEngine(http)

    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="first", url="/first", output_key="f"),
            Step(name="second", url="/second?t={{f.data}}"),
        ],
    )

    result = await engine.run(config, {"sql": "SELECT 1"})
    assert result.rows[0]["y"] == 2


@pytest.mark.asyncio
async def test_not_connected_raises():
    """Test that execute() raises when not connected."""
    config = _make_source_config(
        steps=[{"name": "q", "url": "/q", "output_key": "r"}],
    )
    source = OrchestratedDataSource(config)
    with pytest.raises(DataSourceError) as exc_info:
        await source.execute(QueryRequest(
            source_id="test-orch",
            statement="SELECT 1",
        ))
    assert exc_info.value.code == "NOT_CONNECTED"


@pytest.mark.asyncio
async def test_duplicate_step_name_raises():
    """Test that duplicate step names are rejected."""
    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="a", url="/a", output_key="r1"),
            Step(name="a", url="/a2", output_key="r2"),
        ],
    )
    with pytest.raises(ConfigError) as exc_info:
        _detect_cycles(config)
    assert exc_info.value.code == "DUPLICATE_STEP_NAME"


@pytest.mark.asyncio
async def test_foreach_array_not_list():
    """Test that foreach raises when array_binding is not a list."""
    http = _mock_http_client([
        _mock_response(200, {"data": {"not": "list"}}),
    ])
    engine = OrchestrationEngine(http)

    foreach_step = Step(name="q", url="/q/{{item.id}}", output_key="r")
    config = OrchestrationConfig(
        source_id="test",
        base_url="https://api.example.com",
        steps=[
            Step(name="data", url="/data", output_key="response"),
        ],
        foreach=ForeachConfig(
            array_binding="response",
            item_binding="item",
            step=foreach_step,
            mode="union",
        ),
    )

    with pytest.raises(DataSourceError) as exc_info:
        await engine.run(config, {"sql": "SELECT 1"})
    assert exc_info.value.code == "INVALID_BINDING"


@pytest.mark.asyncio
async def test_schema_info():
    """Test schema_info samples a query to infer schema."""
    http = _mock_http_client([
        _mock_response(200, {"data": [{"id": 1, "name": "test"}]}),
    ])
    config = _make_source_config(
        steps=[{"name": "query", "url": "/data", "output_key": "result"}],
    )
    source = OrchestratedDataSource(config, http_client=http)
    await source.connect()

    schema = await source.schema_info()
    assert len(schema.resources) == 1
    cols = {c.name for c in schema.resources[0].columns}
    assert "id" in cols
    assert "name" in cols
