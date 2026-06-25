"""Token lifecycle manager for two-step auth HTTP API sources.

Orchestrates login → token extraction → SQLite caching → TTL refresh →
token injection, with asyncio.Lock-guaranteed concurrent safety.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any

import httpx2

from deepferry.auth.models import AuthConfig, TokenInfo
from deepferry.core.errors import DataSourceError

if TYPE_CHECKING:
    import aiosqlite


class TokenManager:
    """Manages the full token lifecycle for two-step authentication sources.

    Caches tokens in the ``token_cache`` SQLite table (persistent across process
    restarts) and guarantees that multiple concurrent requests trigger exactly one
    login call via per-source ``asyncio.Lock`` instances.
    """

    def __init__(self, db: aiosqlite.Connection, http_client: httpx2.AsyncClient) -> None:
        self._db = db
        self._http = http_client
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    async def get_token(self, source_id: str, config: AuthConfig) -> str:
        """Return a valid access token for *source_id*.

        1. Check SQLite cache for a non-expired token → return cached.
        2. If expired and ``refresh_url`` exists → try refresh.
        3. Otherwise → login.
        4. Cache the new token and return the access token string.

        Uses ``asyncio.Lock`` per source_id for concurrent safety: only one
        caller performs the actual login/refresh; others wait and read the
        newly-cached token.
        """
        # Fast path: valid cached token
        cached = await self._get_cached_token(source_id)
        if cached is not None:
            return cached.access_token

        # Slow path: acquire per-source lock, double-check, then login/refresh
        lock = self._get_lock(source_id)
        async with lock:
            cached = await self._get_cached_token(source_id)
            if cached is not None:
                return cached.access_token

            # Try refresh if we have an expired token with refresh capability
            expired = await self._get_expired_token(source_id)
            if expired is not None and expired.refresh_token and config.refresh_url:
                try:
                    token = await self._refresh(source_id, config, expired.refresh_token)
                except DataSourceError:
                    token = await self._login(source_id, config)
            else:
                token = await self._login(source_id, config)

        return token.access_token

    async def invalidate(self, source_id: str) -> None:
        """Delete the cached token row for *source_id*.  Idempotent.

        Callers invoke this on a 401 response so that the next ``get_token()``
        re-logs in rather than reusing a server-revoked token.
        """
        await self._db.execute("DELETE FROM token_cache WHERE source_id = ?", (source_id,))
        await self._db.commit()

    @staticmethod
    def apply_token(headers: dict[str, str], token: str, token_type: str) -> dict[str, str]:
        """Inject *token* into the request *headers* dict in-place and return it.

        ====================  ============================================
        ``token_type``         Behaviour
        ====================  ============================================
        ``"bearer"``          ``headers["Authorization"] = "Bearer <token>"``
        ``"header:X-Custom"`` ``headers["X-Custom"] = token``
        ``"query:token"``     No-op — caller handles URL injection.
        ====================  ============================================
        """
        if token_type == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif token_type.startswith("header:"):
            header_name = token_type.split(":", 1)[1]
            headers[header_name] = token
        # "query:token" is handled by the HTTP datasource when building the URL
        return headers

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _extract_nested(data: dict[str, Any], path: str) -> str:
        """Traverse *data* using dot-separated *path* and return the string value."""
        parts = path.split(".")
        current: Any = data
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                raise DataSourceError(
                    code="AUTH_FAILED",
                    message=f"Token field {path!r} not found in response",
                    suggestion="Check the token_field / refresh_token_field configuration",
                )
            current = current[part]
        return str(current)

    def _get_lock(self, source_id: str) -> asyncio.Lock:
        """Return (and cache) a per-source ``asyncio.Lock``.

        Safe to call from multiple coroutines within the same event loop — dict
        operations are not pre-empted by other coroutines.
        """
        if source_id not in self._locks:
            self._locks[source_id] = asyncio.Lock()
        return self._locks[source_id]

    async def _login(self, source_id: str, config: AuthConfig) -> TokenInfo:
        """POST to ``login_url``, extract tokens, persist to SQLite."""
        try:
            response = await self._http.request(
                method=config.login_method,
                url=config.login_url,
                json=config.login_body,
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            raise DataSourceError(
                code="AUTH_FAILED",
                message=f"Login failed for source {source_id!r}: HTTP {exc.response.status_code}",
                suggestion="Check credentials and auth configuration",
            ) from exc
        except httpx2.RequestError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=f"Cannot reach login endpoint for source {source_id!r}: {exc}",
                suggestion="Verify the login_url and network connectivity",
            ) from exc

        data: dict[str, Any] = response.json()
        access_token = self._extract_nested(data, config.token_field)

        expires_at = _compute_expires_at(data, config.token_ttl)

        refresh_token: str | None = None
        if config.refresh_token_field:
            try:
                refresh_token = self._extract_nested(data, config.refresh_token_field)
            except DataSourceError:
                refresh_token = None

        token = TokenInfo(
            source_id=source_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=config.token_type,
            expires_at=expires_at,
            extra=data,
        )
        await self._cache_token(token)
        return token

    async def _refresh(
        self, source_id: str, config: AuthConfig, refresh_token: str
    ) -> TokenInfo:
        """POST to ``refresh_url`` with the current refresh token."""
        assert config.refresh_url is not None  # caller guard

        try:
            response = await self._http.request(
                method="POST",
                url=config.refresh_url,
                json={"refresh_token": refresh_token},
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            raise DataSourceError(
                code="AUTH_FAILED",
                message=f"Token refresh failed for source {source_id!r}: HTTP {exc.response.status_code}",
                suggestion="Credentials may have expired; re-login required",
            ) from exc
        except httpx2.RequestError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=f"Cannot reach refresh endpoint for source {source_id!r}: {exc}",
                suggestion="Verify the refresh_url and network connectivity",
            ) from exc

        data: dict[str, Any] = response.json()
        access_token = self._extract_nested(data, config.token_field)

        expires_at = _compute_expires_at(data, config.token_ttl)

        new_refresh_token: str | None = refresh_token
        if config.refresh_token_field:
            with contextlib.suppress(DataSourceError):
                new_refresh_token = self._extract_nested(data, config.refresh_token_field)

        token = TokenInfo(
            source_id=source_id,
            access_token=access_token,
            refresh_token=new_refresh_token,
            token_type=config.token_type,
            expires_at=expires_at,
            extra=data,
        )
        await self._cache_token(token)
        return token

    async def _cache_token(self, token: TokenInfo) -> None:
        """INSERT OR REPLACE a row into the ``token_cache`` table."""
        extra_json = json.dumps(token.extra) if token.extra else None
        await self._db.execute(
            "INSERT OR REPLACE INTO token_cache "
            "(source_id, access_token, refresh_token, token_type, expires_at, extra, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (
                token.source_id,
                token.access_token,
                token.refresh_token,
                token.token_type,
                token.expires_at,
                extra_json,
            ),
        )
        await self._db.commit()

    async def _get_cached_token(self, source_id: str) -> TokenInfo | None:
        """Return a non-expired token from the cache, or ``None``."""
        cursor = await self._db.execute(
            "SELECT source_id, access_token, refresh_token, token_type, expires_at, extra "
            "FROM token_cache WHERE source_id = ? AND expires_at > ?",
            (source_id, time.time()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_token_info(row)

    async def _get_expired_token(self, source_id: str) -> TokenInfo | None:
        """Return an expired token that has a refresh_token, or ``None``.

        Used by ``get_token()`` to decide whether a refresh is worth attempting
        before falling back to a full re-login.
        """
        cursor = await self._db.execute(
            "SELECT source_id, access_token, refresh_token, token_type, expires_at, extra "
            "FROM token_cache WHERE source_id = ? AND refresh_token IS NOT NULL",
            (source_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_token_info(row)


# ── Module-private helpers ──────────────────────────────────────────────────


def _row_to_token_info(row: Any) -> TokenInfo:
    """Convert a ``token_cache`` row to a ``TokenInfo`` instance."""
    extra_raw = row["extra"] if isinstance(row, dict) else row[5]  # column index fallback
    extra: dict[str, Any] | None = json.loads(extra_raw) if extra_raw else None

    if isinstance(row, dict):
        return TokenInfo(
            source_id=row["source_id"],
            access_token=row["access_token"],
            refresh_token=row.get("refresh_token"),
            token_type=row.get("token_type", "bearer"),
            expires_at=row["expires_at"],
            extra=extra,
        )
    # Fallback for plain tuples / non-Row connections
    return TokenInfo(
        source_id=row[0],
        access_token=row[1],
        refresh_token=row[2],
        token_type=row[3] if row[3] else "bearer",
        expires_at=row[4],
        extra=extra,
    )


def _compute_expires_at(data: dict[str, Any], default_ttl: int) -> float:
    """Determine ``expires_at`` from the response ``expires_in`` field or *default_ttl*."""
    expires_in = data.get("expires_in")
    ttl = int(expires_in) if expires_in is not None else default_ttl
    return time.time() + ttl
