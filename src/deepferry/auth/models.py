"""Pydantic models for two-step authentication configuration and token state."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """Per-source authentication configuration.

    Mirrors the ``[sources.auth]`` TOML section (see two-step-auth spec).
    """

    login_url: str
    """Full URL or path for the login endpoint (e.g. ``https://api.example.com/auth/login``)."""

    login_method: str = "POST"
    """HTTP method for the login request (GET, POST, etc.)."""

    login_body: dict[str, str]
    """JSON body sent to the login endpoint. Values may contain ``${ENV_VAR}`` placeholders
    that are resolved before the request is dispatched."""

    token_field: str = "access_token"
    """Dot-notation JSON path to extract the access token from the login response
    (e.g. ``"data.access_token"``)."""

    token_type: str = "bearer"
    """Token injection method:
    ``"bearer"`` → ``Authorization: Bearer <token>``,
    ``"header:X-Custom"`` → ``X-Custom: <token>``,
    ``"query:token"`` → appended to URL by the caller."""

    token_ttl: int = Field(default=3600, ge=1)
    """Default token lifetime in seconds. Overridden by ``expires_in`` in the login
    response when present."""

    refresh_url: str | None = None
    """Optional refresh endpoint. When set and the cached token is expired, the
    manager attempts a refresh before falling back to a full re-login."""

    refresh_token_field: str | None = None
    """Dot-notation JSON path to extract a refresh token from the login response."""


class TokenInfo(BaseModel):
    """In-memory representation of a cached token row."""

    source_id: str
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_at: float
    """Unix timestamp (seconds since epoch) when this token expires."""
    extra: dict[str, Any] | None = None
    """Additional fields from the login/refresh response, serialisable as JSON."""
