"""Diagnostics engine for the deepferry agent monitor.

Maps execution/error contexts to one of 4 diagnosis categories (TIMEOUT,
AUTH, CONNECTION, QUERY) or UNKNOWN, each with a human-readable explanation
and an actionable remediation suggestion.

The engine uses a priority-ordered list of rule callables.  The first
matching rule wins — a timeout that also mentions "connection" still
emits TIMEOUT because timeout is checked first.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from deepferry.core.trace import Execution


# ── Output model ─────────────────────────────────────────────────────────


class DiagnosisCategory(StrEnum):
    """Top-level classification for a diagnosed error."""

    CONNECTION = "connection"
    AUTH = "auth"
    TIMEOUT = "timeout"
    QUERY = "query"
    UNKNOWN = "unknown"


class Diagnosis(BaseModel):
    """Structured diagnosis produced by the diagnostics engine.

    Attributes
    ----------
    category : DiagnosisCategory
        Broad classification (TIMEOUT, AUTH, CONNECTION, QUERY, UNKNOWN).
    title : str
        Short human label, e.g. "Database connection failed".
    diagnosis : str
        1-2 sentence explanation of the root cause.
    suggestion : str
        Actionable remediation step the agent or user can take.
    confidence : float
        Confidence score between 0.0 and 1.0.
    """

    category: DiagnosisCategory
    title: str
    diagnosis: str
    suggestion: str
    confidence: float = 1.0


# ── Rule type ────────────────────────────────────────────────────────────

# A rule receives the lowercased error code, lowercased error message, and
# an optional list of span dicts.  It returns a Diagnosis if it matches, or
# None if it does not.
Rule = Callable[
    [str | None, str, list[dict[str, Any]] | None],
    Diagnosis | None,
]


# ── Helper ───────────────────────────────────────────────────────────────


def _span_duration_ms(span: dict[str, Any]) -> float | None:
    """Extract span duration in milliseconds.

    Prefers an explicit ``duration_ms`` key; falls back to computing from
    ``started_at`` / ``finished_at``.
    """
    if "duration_ms" in span and span["duration_ms"] is not None:
        return float(span["duration_ms"])
    started = span.get("started_at")
    finished = span.get("finished_at")
    if started is not None and finished is not None:
        return float(finished) - float(started)
    return None


# ── Rules (priority-ordered) ─────────────────────────────────────────────


def _rule_timeout(
    code_lower: str | None,
    msg_lower: str,
    spans: list[dict[str, Any]] | None,
) -> Diagnosis | None:
    """Match timeout errors.

    Triggers on:
    - error code ``TIMEOUT`` / ``LLM_TIMEOUT``
    - any span with duration > 30 s AND status == "error"
    - error message contains "timeout" or "timed out"
    """
    if code_lower in {"timeout", "llm_timeout"}:
        return Diagnosis(
            category=DiagnosisCategory.TIMEOUT,
            title="Query execution timed out",
            diagnosis="The query exceeded the configured or database-level "
            "statement timeout.",
            suggestion="Reduce the result set (add LIMIT/WHERE), add database "
            "indexes on filtered columns, or raise the statement "
            "timeout in config.toml.",
        )

    if spans:
        for span in spans:
            dur = _span_duration_ms(span)
            if dur is not None and dur > 30000 and span.get("status") == "error":
                return Diagnosis(
                    category=DiagnosisCategory.TIMEOUT,
                    title="Query execution timed out",
                    diagnosis="A span took longer than 30 seconds and ended in "
                    "error, indicating a timeout.",
                    suggestion="Reduce the result set (add LIMIT/WHERE), add database "
                    "indexes on filtered columns, or raise the statement "
                    "timeout in config.toml.",
                )

    if "timeout" in msg_lower or "timed out" in msg_lower:
        return Diagnosis(
            category=DiagnosisCategory.TIMEOUT,
            title="Query execution timed out",
            diagnosis="The error message indicates a timeout occurred during "
            "query execution.",
            suggestion="Reduce the result set (add LIMIT/WHERE), add database "
            "indexes on filtered columns, or raise the statement "
            "timeout in config.toml.",
        )

    return None


def _rule_auth(
    code_lower: str | None,
    msg_lower: str,
    spans: list[dict[str, Any]] | None,
) -> Diagnosis | None:
    """Match authentication / authorization errors.

    Triggers on:
    - error code ``AUTH_FAILED``
    - any span with ``span_kind == "auth_retry"``
    - error message contains "401", "unauthorized", "authentication", or "token"
    """
    if code_lower == "auth_failed":
        return Diagnosis(
            category=DiagnosisCategory.AUTH,
            title="Authentication failed",
            diagnosis="The data source rejected the credentials or the "
            "authentication token expired / failed to refresh.",
            suggestion="Verify the source credentials in config.toml and confirm "
            "the auth endpoint is reachable. For token-based sources, "
            "check token expiry and refresh configuration.",
        )

    if spans:
        for span in spans:
            if span.get("span_kind") == "auth_retry":
                return Diagnosis(
                    category=DiagnosisCategory.AUTH,
                    title="Authentication failed",
                    diagnosis="An auth_retry span was recorded, indicating a "
                    "failed authentication attempt that triggered a retry.",
                    suggestion="Verify the source credentials in config.toml and confirm "
                    "the auth endpoint is reachable. For token-based sources, "
                    "check token expiry and refresh configuration.",
                )

    auth_markers = ("401", "unauthorized", "authentication", "token")
    if any(marker in msg_lower for marker in auth_markers):
        return Diagnosis(
            category=DiagnosisCategory.AUTH,
            title="Authentication failed",
            diagnosis="The error message indicates an authentication or "
            "authorization failure.",
            suggestion="Verify the source credentials in config.toml and confirm "
            "the auth endpoint is reachable. For token-based sources, "
            "check token expiry and refresh configuration.",
        )

    return None


def _rule_connection(
    code_lower: str | None,
    msg_lower: str,
    spans: list[dict[str, Any]] | None,  # noqa: ARG001  unused but part of Rule signature
) -> Diagnosis | None:
    """Match connection errors.

    Triggers on:
    - error code ``CONNECTION_FAILED``
    - error message contains "connect", "connection refused", "unreachable",
      "econnrefused", "host", or "port"
    """
    if code_lower == "connection_failed":
        return Diagnosis(
            category=DiagnosisCategory.CONNECTION,
            title="Data source unreachable",
            diagnosis="The data source rejected or did not respond to the "
            "connection attempt.",
            suggestion="Confirm the source host/port are correct, the database/API "
            "is running, and network/firewall rules allow the connection.",
        )

    conn_markers = (
        "connect",
        "connection refused",
        "unreachable",
        "econnrefused",
        "host",
        "port",
    )
    if any(marker in msg_lower for marker in conn_markers):
        return Diagnosis(
            category=DiagnosisCategory.CONNECTION,
            title="Data source unreachable",
            diagnosis="The error message indicates a connectivity issue with "
            "the data source.",
            suggestion="Confirm the source host/port are correct, the database/API "
            "is running, and network/firewall rules allow the connection.",
        )

    return None


def _rule_query(
    code_lower: str | None,
    msg_lower: str,
    spans: list[dict[str, Any]] | None,  # noqa: ARG001  unused but part of Rule signature
) -> Diagnosis | None:
    """Match query / schema / permission errors.

    Triggers on:
    - error code in {QUERY_FAILED, DATASOURCE_ERROR, LLM_INVALID_SQL, VALIDATION_ERROR}
    - error message contains "syntax", "sql", "column", "table",
      "permission", or "denied"
    """
    if code_lower in {"query_failed", "datasource_error", "llm_invalid_sql", "validation_error"}:
        return Diagnosis(
            category=DiagnosisCategory.QUERY,
            title="SQL execution failed",
            diagnosis="The query was rejected by the source — possible syntax "
            "error, unknown object reference, or insufficient permissions.",
            suggestion="Check the SQL syntax against the source schema, verify "
            "referenced tables/columns exist, and confirm the user has "
            "SELECT permission.",
        )

    query_markers = ("syntax", "sql", "column", "table", "permission", "denied")
    if any(marker in msg_lower for marker in query_markers):
        return Diagnosis(
            category=DiagnosisCategory.QUERY,
            title="SQL execution failed",
            diagnosis="The error message indicates a query-level failure such as "
            "a syntax error, missing object, or permission denial.",
            suggestion="Check the SQL syntax against the source schema, verify "
            "referenced tables/columns exist, and confirm the user has "
            "SELECT permission.",
        )

    return None


# ── Rule list (priority-ordered — first match wins) ─────────────────────

_RULES: list[Rule] = [
    _rule_timeout,
    _rule_auth,
    _rule_connection,
    _rule_query,
]


# ── Public API ───────────────────────────────────────────────────────────


def diagnose(
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    spans: list[dict[str, Any]] | None = None,
) -> Diagnosis:
    """Classify an error into one of 4 diagnostic categories with remediation guidance.

    Evaluates rules in priority order (TIMEOUT → AUTH → CONNECTION → QUERY)
    and returns the first matching diagnosis.  Falls back to UNKNOWN with
    low confidence when no rule matches.

    Parameters
    ----------
    error_code : str or None
        Machine-readable error code (e.g. ``"TIMEOUT"``, ``"AUTH_FAILED"``).
    error_message : str or None
        Human-readable error message or exception text.
    spans : list[dict] or None
        Optional list of span dicts.  Each dict may contain keys:
        ``span_kind`` (str), ``status`` (str), ``duration_ms``
        (float|None), ``started_at`` (int), ``finished_at`` (int),
        and ``error`` (str|None).

    Returns
    -------
    Diagnosis
        Structured diagnosis with category, explanation, and suggestion.
    """
    code_lower = error_code.lower() if error_code else None
    msg_lower = error_message.lower() if error_message else ""

    for rule in _RULES:
        result = rule(code_lower, msg_lower, spans)
        if result is not None:
            return result

    return Diagnosis(
        category=DiagnosisCategory.UNKNOWN,
        title="Unknown error",
        diagnosis=error_message or "An unrecognised error occurred.",
        suggestion="Inspect the execution trace spans for more detail.",
        confidence=0.3,
    )


def diagnose_execution(execution: Execution) -> Diagnosis:
    """Diagnose from a trace ``Execution`` model.

    Derives the error code and message from the first span whose status is
    ``"error"``, then delegates to :func:`diagnose`.  Also passes the full
    span list so that duration-based timeout and auth_retry rules can fire.

    Parameters
    ----------
    execution : deepferry.core.trace.Execution
        A trace execution containing at least one span.

    Returns
    -------
    Diagnosis
    """
    from deepferry.core.trace import SpanStatus  # local import avoids cycles

    error_code: str | None = None
    error_message: str | None = None
    spans_dicts: list[dict[str, Any]] = []

    for span in execution.spans:
        dur: float | None = None
        if span.finished_at is not None:
            dur = float(span.finished_at - span.started_at)

        span_dict: dict[str, Any] = {
            "span_kind": str(span.span_kind.value),
            "status": str(span.status.value),
            "duration_ms": dur,
            "error": None,
        }

        if span.status == SpanStatus.error:
            attrs = span.attributes
            err_code = attrs.get("error_code")
            if err_code is not None and error_code is None:
                error_code = str(err_code)
            err_msg = attrs.get("error_message")
            if err_msg is not None and error_message is None:
                error_message = str(err_msg)
            # Fallback: use span_name as error message
            if error_message is None:
                error_message = span.span_name
            span_dict["error"] = error_message

        spans_dicts.append(span_dict)

    return diagnose(
        error_code=error_code,
        error_message=error_message,
        spans=spans_dicts,
    )
