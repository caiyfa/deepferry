"""Unit tests for core.diagnostics — 4-rule diagnosis engine."""

from __future__ import annotations

import pytest

from deepferry.core.diagnostics import Diagnosis, DiagnosisCategory, diagnose, diagnose_execution
from deepferry.core.trace import Execution, Span, SpanKind, SpanStatus

# ── Parametric: error code → category ────────────────────────────────────


@pytest.mark.parametrize(
    "error_code, expected_category, expected_title",
    [
        ("TIMEOUT", DiagnosisCategory.TIMEOUT, "Query execution timed out"),
        ("LLM_TIMEOUT", DiagnosisCategory.TIMEOUT, "Query execution timed out"),
        ("AUTH_FAILED", DiagnosisCategory.AUTH, "Authentication failed"),
        ("CONNECTION_FAILED", DiagnosisCategory.CONNECTION, "Data source unreachable"),
        ("QUERY_FAILED", DiagnosisCategory.QUERY, "SQL execution failed"),
        ("DATASOURCE_ERROR", DiagnosisCategory.QUERY, "SQL execution failed"),
        ("LLM_INVALID_SQL", DiagnosisCategory.QUERY, "SQL execution failed"),
        ("VALIDATION_ERROR", DiagnosisCategory.QUERY, "SQL execution failed"),
    ],
)
def test_diagnose_by_error_code(
    error_code: str,
    expected_category: DiagnosisCategory,
    expected_title: str,
) -> None:
    result = diagnose(error_code=error_code)
    assert result.category == expected_category
    assert result.title == expected_title
    assert result.confidence == 1.0


# ── Parametric: error message substring → category ───────────────────────


@pytest.mark.parametrize(
    "error_message, expected_category",
    [
        # TIMEOUT messages
        ("Connection timed out after 30s", DiagnosisCategory.TIMEOUT),
        ("request timeout exceeded", DiagnosisCategory.TIMEOUT),
        # AUTH messages
        ("HTTP 401 Unauthorized", DiagnosisCategory.AUTH),
        ("unauthorized access to resource", DiagnosisCategory.AUTH),
        ("authentication failed for user", DiagnosisCategory.AUTH),
        ("token expired at 2025-01-01", DiagnosisCategory.AUTH),
        # CONNECTION messages
        ("cannot connect to host", DiagnosisCategory.CONNECTION),
        ("connection refused by server", DiagnosisCategory.CONNECTION),
        ("destination unreachable", DiagnosisCategory.CONNECTION),
        ("econnrefused on port 3306", DiagnosisCategory.CONNECTION),
        ("unknown host: db.example.com", DiagnosisCategory.CONNECTION),
        # QUERY messages
        ("syntax error in SQL statement", DiagnosisCategory.QUERY),
        ("column 'user_id' does not exist", DiagnosisCategory.QUERY),
        ("permission denied for table customers", DiagnosisCategory.QUERY),
        ("access denied to schema public", DiagnosisCategory.QUERY),
    ],
)
def test_diagnose_by_error_message(
    error_message: str,
    expected_category: DiagnosisCategory,
) -> None:
    result = diagnose(error_message=error_message)
    assert result.category == expected_category
    assert result.confidence == 1.0


# ── Span-based rules ─────────────────────────────────────────────────────


def test_diagnose_timeout_via_span_duration() -> None:
    """A span with duration > 30s and status=error triggers TIMEOUT."""
    spans: list[dict[str, object]] = [
        {
            "span_kind": "sql_exec",
            "status": "error",
            "duration_ms": 45000.0,
        },
    ]
    result = diagnose(spans=spans)
    assert result.category == DiagnosisCategory.TIMEOUT
    assert "30 seconds" in result.diagnosis
    assert result.confidence == 1.0


def test_diagnose_timeout_via_span_started_finished() -> None:
    """Duration computed from started_at/finished_at also triggers TIMEOUT."""
    spans: list[dict[str, object]] = [
        {
            "span_kind": "sql_exec",
            "status": "error",
            "started_at": 1000,
            "finished_at": 35000,  # 34 s
        },
    ]
    result = diagnose(spans=spans)
    assert result.category == DiagnosisCategory.TIMEOUT


def test_diagnose_auth_via_span_kind() -> None:
    """A span with span_kind='auth_retry' triggers AUTH."""
    spans: list[dict[str, object]] = [
        {
            "span_kind": "auth_retry",
            "status": "ok",
            "duration_ms": 200.0,
        },
    ]
    result = diagnose(spans=spans)
    assert result.category == DiagnosisCategory.AUTH
    assert result.title == "Authentication failed"
    assert result.confidence == 1.0


def test_diagnose_no_timeout_when_duration_under_30s() -> None:
    """Duration under 30s should NOT trigger TIMEOUT."""
    spans: list[dict[str, object]] = [
        {
            "span_kind": "sql_exec",
            "status": "error",
            "duration_ms": 25000.0,
        },
    ]
    result = diagnose(spans=spans, error_message="something went wrong")
    assert result.category != DiagnosisCategory.TIMEOUT


# ── Priority order ───────────────────────────────────────────────────────


def test_timeout_wins_over_connection_in_message() -> None:
    """TIMEOUT is higher priority; a timeout message mentioning 'host'
    still yields TIMEOUT."""
    result = diagnose(error_message="timeout: cannot connect to host db.local:3306")
    assert result.category == DiagnosisCategory.TIMEOUT


def test_auth_wins_over_query_in_message() -> None:
    """AUTH is higher priority than QUERY."""
    result = diagnose(error_message="unauthorized: permission denied for table")
    assert result.category == DiagnosisCategory.AUTH


def test_error_code_wins_over_message() -> None:
    """An explicit error code takes priority over message substring rules
    within the same rule."""
    result = diagnose(
        error_code="AUTH_FAILED",
        error_message="syntax error near SELECT",  # would match QUERY
    )
    assert result.category == DiagnosisCategory.AUTH


# ── UNKNOWN fallback ─────────────────────────────────────────────────────


def test_diagnose_unknown_no_match() -> None:
    """When nothing matches, fall back to UNKNOWN with low confidence."""
    result = diagnose(error_message="an obscure internal failure")
    assert result.category == DiagnosisCategory.UNKNOWN
    assert result.confidence == 0.3
    assert result.title == "Unknown error"
    assert result.diagnosis == "an obscure internal failure"


def test_diagnose_unknown_empty() -> None:
    """Empty inputs yield UNKNOWN."""
    result = diagnose()
    assert result.category == DiagnosisCategory.UNKNOWN
    assert result.confidence == 0.3
    assert "unrecognised" in result.diagnosis.lower()


# ── diagnose_execution ───────────────────────────────────────────────────


def make_execution(
    *,
    status: SpanStatus = SpanStatus.error,
    span_kind: SpanKind = SpanKind.sql_exec,
    span_name: str = "SELECT * FROM customers",
    span_status: SpanStatus = SpanStatus.error,
    started_at: int = 1000,
    finished_at: int = 1200,
    attrs: dict[str, str | int | float | bool] | None = None,
) -> Execution:
    """Build a minimal Execution with a single span for diagnostics testing."""
    span_attrs: dict[str, str | int | float | bool] = attrs or {}
    span = Span(
        id=1,
        execution_id=1,
        parent_span_id=None,
        span_kind=span_kind,
        span_name=span_name,
        source_id="test-db",
        started_at=started_at,
        finished_at=finished_at,
        status=span_status,
        attributes=span_attrs,
    )
    return Execution(
        id=1,
        source_id="test-db",
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        spans=[span],
    )


def test_diagnose_execution_from_span_attributes() -> None:
    """Extracts error_code/error_message from span attributes."""
    exe = make_execution(
        attrs={"error_code": "CONNECTION_FAILED", "error_message": "connection refused to 10.0.0.1:3306"},
    )
    result = diagnose_execution(exe)
    assert result.category == DiagnosisCategory.CONNECTION
    assert result.title == "Data source unreachable"


def test_diagnose_execution_falls_back_to_span_name() -> None:
    """When attributes lack error_message, the span_name is used."""
    exe = make_execution(
        span_name="timed out after 30s",
        attrs={"error_code": "TIMEOUT"},
    )
    result = diagnose_execution(exe)
    assert result.category == DiagnosisCategory.TIMEOUT


def test_diagnose_execution_auth_retry_span() -> None:
    """An auth_retry span triggers AUTH even without an explicit error code."""
    exe = make_execution(
        status=SpanStatus.ok,
        span_kind=SpanKind.auth_retry,
        span_status=SpanStatus.ok,
        span_name="token refresh attempt",
    )
    result = diagnose_execution(exe)
    assert result.category == DiagnosisCategory.AUTH


def test_diagnose_execution_duration_timeout() -> None:
    """A span running longer than 30s triggers TIMEOUT."""
    exe = make_execution(
        started_at=0,
        finished_at=35000,
        span_name="long running query",
    )
    result = diagnose_execution(exe)
    assert result.category == DiagnosisCategory.TIMEOUT


def test_diagnose_execution_no_error_span() -> None:
    """When no span has error status, falls back to UNKNOWN."""
    span = Span(
        id=1,
        execution_id=1,
        span_kind=SpanKind.sql_exec,
        span_name="SELECT 1",
        source_id="test-db",
        started_at=1000,
        finished_at=1100,
        status=SpanStatus.ok,
        attributes={"row_count": 1},
    )
    exe = Execution(
        id=1,
        source_id="test-db",
        started_at=1000,
        finished_at=1100,
        status=SpanStatus.ok,
        spans=[span],
    )
    result = diagnose_execution(exe)
    assert result.category == DiagnosisCategory.UNKNOWN
    assert result.confidence == 0.3


# ── Case insensitivity ───────────────────────────────────────────────────


def test_case_insensitive_matching() -> None:
    """Error codes and messages are matched case-insensitively."""
    result = diagnose(error_code="Timeout", error_message="Connection timed OUT")
    assert result.category == DiagnosisCategory.TIMEOUT


# ── Diagnosis model ──────────────────────────────────────────────────────


def test_diagnosis_model_defaults() -> None:
    d = Diagnosis(
        category=DiagnosisCategory.UNKNOWN,
        title="test",
        diagnosis="test diagnosis",
        suggestion="test suggestion",
    )
    assert d.confidence == 1.0
    assert d.model_dump()["category"] == "unknown"
