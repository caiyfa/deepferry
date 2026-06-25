"""Structured error hierarchy for the deepferry data access layer.

Every error that crosses the MCP tool boundary is a subclass of DeepFerryError
and carries a machine-readable ``code``, a human-readable ``message``, and an
optional ``suggestion`` for remediation.  Agents never see raw Python tracebacks.
"""

from __future__ import annotations


class DeepFerryError(Exception):
    """Base exception for all deepferry errors.

    Each subclass provides a default ``code`` but callers may override it.
    The ``to_dict()`` method produces the JSON-safe envelope that MCP tools
    return to agents.
    """

    code: str
    message: str
    suggestion: str | None
    status_code: int

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        suggestion: str | None = None,
        status_code: int = 500,
    ) -> None:
        self.code = code or self.__class__.code
        self.message = message or "An unexpected error occurred."
        self.suggestion = suggestion
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str | None]:
        """JSON-safe representation suitable for MCP tool responses."""
        result: dict[str, str | None] = {
            "code": self.code,
            "message": self.message,
        }
        if self.suggestion is not None:
            result["suggestion"] = self.suggestion
        return result


class ConfigError(DeepFerryError):
    """Errors originating from invalid config.toml."""

    code = "INVALID_CONFIG"
    status_code = 500


class DataSourceError(DeepFerryError):
    """Errors originating from data source operations.

    Common codes: CONNECTION_FAILED, QUERY_FAILED, TIMEOUT, AUTH_FAILED,
    UNSUPPORTED_OPERATION.
    """

    code = "DATASOURCE_ERROR"
    status_code = 502


class SourceNotFoundError(DeepFerryError):
    """A referenced source_id does not exist in the registry."""

    code = "SOURCE_NOT_FOUND"
    status_code = 404


class ValidationError(DeepFerryError):
    """Invalid query parameters or request shape."""

    code = "VALIDATION_ERROR"
    status_code = 400
