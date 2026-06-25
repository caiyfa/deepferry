"""Unit tests for the two-step authentication module.

Uses an in-memory SQLite database and a mocked ``httpx2.AsyncClient`` to
exercise every path through ``TokenManager`` without network I/O.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import httpx2
import pytest

from deepferry.auth.models import AuthConfig
from deepferry.auth.token_manager import TokenManager
from deepferry.core.errors import DataSourceError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# ── Constants ────────────────────────────────────────────────────────────

_SOURCE_ID = "protected-api"
_LOGIN_URL = "https://api.example.com/auth/login"


def _make_config(**overrides: Any) -> AuthConfig:
    defaults: dict[str, Any] = {
        "login_url": _LOGIN_URL,
        "login_method": "POST",
        "login_body": {"username": "testuser", "password": "testpass"},
        "token_field": "access_token",
        "token_type": "bearer",
        "token_ttl": 3600,
    }
    defaults.update(overrides)
    return AuthConfig(**defaults)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection, None]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_cache (
            source_id      TEXT PRIMARY KEY,
            access_token   TEXT NOT NULL,
            refresh_token  TEXT,
            token_type     TEXT NOT NULL DEFAULT 'bearer',
            expires_at     REAL NOT NULL,
            extra          TEXT,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def mock_http() -> AsyncMock:
    return AsyncMock(spec=httpx2.AsyncClient)


@pytest.fixture
async def manager(
    db: aiosqlite.Connection, mock_http: AsyncMock
) -> TokenManager:
    return TokenManager(db=db, http_client=mock_http)


def _mock_response(json_data: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Build a mock ``httpx2.Response`` with ``.json()`` and ``.raise_for_status()``."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if 400 <= status_code < 600:
        error = httpx2.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp
        )
        resp.raise_for_status.side_effect = error
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_and_cache_token(manager: TokenManager, mock_http: AsyncMock) -> None:
    """First call triggers login; token is cached for subsequent calls."""
    config = _make_config()
    mock_http.request.return_value = _mock_response(
        {"access_token": "abc123", "expires_in": 3600}
    )

    token = await manager.get_token(_SOURCE_ID, config)

    assert token == "abc123"
    mock_http.request.assert_awaited_once()

    # Verify token is cached in SQLite
    cursor = await manager._db.execute(
        "SELECT access_token, expires_at FROM token_cache WHERE source_id = ?",
        (_SOURCE_ID,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["access_token"] == "abc123"
    assert row["expires_at"] > time.time()


@pytest.mark.asyncio
async def test_get_token_returns_cached_when_valid(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """Second call after caching must NOT trigger a new HTTP request."""
    config = _make_config()

    # Pre-populate cache with valid token
    future_time = time.time() + 3600
    await manager._db.execute(
        "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
        "VALUES (?, ?, 'bearer', ?)",
        (_SOURCE_ID, "cached-token", future_time),
    )
    await manager._db.commit()

    token = await manager.get_token(_SOURCE_ID, config)

    assert token == "cached-token"
    mock_http.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_token_refreshes_when_expired(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """Expired token with refresh_token → refresh is called, new token cached."""
    config = _make_config(
        refresh_url="https://api.example.com/auth/refresh",
        refresh_token_field="refresh_token",
    )

    # Pre-populate with expired token
    past_time = time.time() - 3600
    await manager._db.execute(
        "INSERT INTO token_cache (source_id, access_token, refresh_token, "
        "token_type, expires_at) VALUES (?, ?, ?, 'bearer', ?)",
        (_SOURCE_ID, "old-token", "rt-old", past_time),
    )
    await manager._db.commit()

    # Mock refresh response
    mock_http.request.return_value = _mock_response(
        {"access_token": "new-token", "expires_in": 3600, "refresh_token": "rt-new"}
    )

    token = await manager.get_token(_SOURCE_ID, config)

    assert token == "new-token"
    mock_http.request.assert_awaited_once()

    # Verify POST to refresh URL
    call_args = mock_http.request.await_args
    assert call_args.kwargs["url"] == "https://api.example.com/auth/refresh"
    assert call_args.kwargs["json"] == {"refresh_token": "rt-old"}


@pytest.mark.asyncio
async def test_get_token_falls_back_to_login_when_refresh_fails(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """When refresh returns an HTTP error, login is attempted instead."""
    config = _make_config(
        refresh_url="https://api.example.com/auth/refresh",
        refresh_token_field="refresh_token",
    )

    past_time = time.time() - 3600
    await manager._db.execute(
        "INSERT INTO token_cache (source_id, access_token, refresh_token, "
        "token_type, expires_at) VALUES (?, ?, ?, 'bearer', ?)",
        (_SOURCE_ID, "old-token", "rt-old", past_time),
    )
    await manager._db.commit()

    # First call: refresh fails with 401
    # Second call: login succeeds
    mock_http.request.side_effect = [
        _mock_response({}, 401),
        _mock_response({"access_token": "fresh-token", "expires_in": 3600}),
    ]

    token = await manager.get_token(_SOURCE_ID, config)

    assert token == "fresh-token"
    assert mock_http.request.await_count == 2


@pytest.mark.asyncio
async def test_get_token_login_http_error(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """Login HTTP error → AUTH_FAILED DataSourceError."""
    config = _make_config()
    mock_http.request.return_value = _mock_response({"error": "unauthorized"}, 401)

    with pytest.raises(DataSourceError) as exc_info:
        await manager.get_token(_SOURCE_ID, config)
    assert exc_info.value.code == "AUTH_FAILED"


@pytest.mark.asyncio
async def test_get_token_login_network_error(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """Login network error → CONNECTION_FAILED DataSourceError."""
    config = _make_config()
    mock_http.request.side_effect = httpx2.ConnectError("connection refused")

    with pytest.raises(DataSourceError) as exc_info:
        await manager.get_token(_SOURCE_ID, config)
    assert exc_info.value.code == "CONNECTION_FAILED"


@pytest.mark.asyncio
async def test_concurrent_access_single_login(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """5 concurrent get_token calls → login endpoint called exactly once."""
    config = _make_config()

    call_count = 0
    login_called = asyncio.Event()

    async def slow_login(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        login_called.set()
        # Simulate network delay so other coroutines queue up
        await asyncio.sleep(0.05)
        return _mock_response({"access_token": "shared-token", "expires_in": 3600})

    mock_http.request.side_effect = slow_login

    results = await asyncio.gather(
        manager.get_token(_SOURCE_ID, config),
        manager.get_token(_SOURCE_ID, config),
        manager.get_token(_SOURCE_ID, config),
        manager.get_token(_SOURCE_ID, config),
        manager.get_token(_SOURCE_ID, config),
    )

    assert list(results) == ["shared-token"] * 5
    assert call_count == 1, f"Expected 1 login call, got {call_count}"


@pytest.mark.asyncio
async def test_invalidate_removes_token(manager: TokenManager, mock_http: AsyncMock) -> None:
    """invalidate() deletes the cached row; next get_token() triggers login."""
    config = _make_config()

    # Pre-populate valid token
    future_time = time.time() + 3600
    await manager._db.execute(
        "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
        "VALUES (?, ?, 'bearer', ?)",
        (_SOURCE_ID, "old-valid", future_time),
    )
    await manager._db.commit()

    # Invalidate
    await manager.invalidate(_SOURCE_ID)

    # Verify row is gone
    cursor = await manager._db.execute(
        "SELECT COUNT(*) AS cnt FROM token_cache WHERE source_id = ?",
        (_SOURCE_ID,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["cnt"] == 0

    # Next get_token should trigger login
    mock_http.request.return_value = _mock_response(
        {"access_token": "new-token", "expires_in": 3600}
    )
    token = await manager.get_token(_SOURCE_ID, config)
    assert token == "new-token"
    mock_http.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_token_bearer() -> None:
    headers: dict[str, str] = {}
    TokenManager.apply_token(headers, "my-token", "bearer")
    assert headers["Authorization"] == "Bearer my-token"


@pytest.mark.asyncio
async def test_apply_token_custom_header() -> None:
    headers: dict[str, str] = {}
    TokenManager.apply_token(headers, "my-token", "header:X-API-Key")
    assert headers["X-API-Key"] == "my-token"
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_apply_token_query_noop() -> None:
    headers: dict[str, str] = {}
    TokenManager.apply_token(headers, "my-token", "query:token")
    assert headers == {}


@pytest.mark.asyncio
async def test_extract_nested_token(manager: TokenManager, mock_http: AsyncMock) -> None:
    """Token field uses dot-notation path like 'data.access_token'."""
    config = _make_config(token_field="data.access_token")
    mock_http.request.return_value = _mock_response(
        {"data": {"access_token": "nested123", "expires_in": 3600}}
    )
    token = await manager.get_token(_SOURCE_ID, config)
    assert token == "nested123"


@pytest.mark.asyncio
async def test_uses_expires_in_from_response(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """Token TTL from response's 'expires_in' overrides config.token_ttl."""
    config = _make_config(token_ttl=99999)
    mock_http.request.return_value = _mock_response(
        {"access_token": "abc", "expires_in": 60}
    )

    await manager.get_token(_SOURCE_ID, config)

    cursor = await manager._db.execute(
        "SELECT expires_at FROM token_cache WHERE source_id = ?",
        (_SOURCE_ID,),
    )
    row = await cursor.fetchone()
    assert row is not None
    expires_at = row["expires_at"]
    # Should be roughly now + 60, NOT now + 99999
    assert expires_at < time.time() + 120
    assert expires_at > time.time() + 30


@pytest.mark.asyncio
async def test_invalidate_idempotent(manager: TokenManager) -> None:
    """invalidate() on a non-existent source_id does not raise."""
    await manager.invalidate("non-existent-source")


@pytest.mark.asyncio
async def test_concurrent_invalidate_and_get_token(
    manager: TokenManager, mock_http: AsyncMock
) -> None:
    """5 concurrent get_token calls after invalidation → login called exactly once."""
    config = _make_config()

    future_time = time.time() + 3600
    await manager._db.execute(
        "INSERT INTO token_cache (source_id, access_token, token_type, expires_at) "
        "VALUES (?, ?, 'bearer', ?)",
        (_SOURCE_ID, "will-be-invalidated", future_time),
    )
    await manager._db.commit()

    call_count = 0

    async def counted_login(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.03)
        return _mock_response({"access_token": f"fresh-{call_count}", "expires_in": 3600})

    mock_http.request.side_effect = counted_login

    lock = manager.acquire_lock(_SOURCE_ID)

    async with lock:
        await manager.invalidate(_SOURCE_ID)
        tasks = [manager.get_token(_SOURCE_ID, config) for _ in range(5)]
        await asyncio.sleep(0.02)

    results = await asyncio.gather(*tasks)
    assert results == ["fresh-1"] * 5
    assert call_count == 1, f"Expected 1 login, got {call_count}"
