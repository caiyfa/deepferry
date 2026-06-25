# Capability: Two-Step Authentication

> **Status**: planned | **Milestone**: M2 | **Owner**: backend | **Differentiator**: ⭐

## Summary

Enable HTTP API data sources that require a preliminary login step to obtain an authentication token, which is then injected into subsequent data requests. Handle token caching, TTL-based refresh, and concurrent access safety.

## Motivation

- Many production APIs require "login → get token → use token" flow
- **No existing open-source tool supports this for agent data access**
- This is the primary technical differentiator — the "killer feature" for interviews

## Specification

### Configuration

```toml
[[sources]]
id = "protected-api"
type = "http"
base_url = "https://api.example.com/v1"

# Two-step authentication configuration
[sources.auth]
login_url = "/auth/login"
login_method = "POST"
login_body = { username = "${API_USER}", password = "${API_PASS}" }
token_field = "access_token"           # JSON path to extract token
token_type = "bearer"                  # "bearer" | "header:X-Custom" | "query:token"
token_ttl = 3600                        # seconds, or extract from "expires_in" field
refresh_url = "/auth/refresh"          # optional, if supported
refresh_token_field = "refresh_token"  # JSON path in login response
```

### Token Lifecycle

```
┌─────────┐     ┌──────────────┐     ┌──────────────┐
│  Agent  │────▶│  MCP Server   │────▶│  HTTP Source  │
│  Query  │     │               │     │               │
└─────────┘     └──────┬───────┘     └───────┬───────┘
                       │                     │
                       │    ┌────────────┐   │
                       │    │ Token Cache│   │
                       │    │  (SQLite)  │   │
                       │    └─────┬──────┘   │
                       │          │          │
              ┌────────▼──────────▼──────────▼────────┐
              │           Token Resolution             │
              │                                       │
              │  has valid cached token?               │
              │  ├── YES → inject into request         │
              │  └── NO  → POST /auth/login            │
              │           │                            │
              │           ├── success → cache token    │
              │           │   (source_id, token,       │
              │           │    expires_at, refresh)    │
              │           │                            │
              │           └── fail → AUTH_FAILED error │
              └───────────────────────────────────────┘
```

### Token Caching (SQLite)

```sql
CREATE TABLE token_cache (
    source_id   TEXT PRIMARY KEY,
    token       TEXT NOT NULL,
    token_type  TEXT NOT NULL,
    expires_at  INTEGER NOT NULL,
    refresh_token TEXT,
    extra       TEXT,  -- JSON for custom fields
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
```

Persistence means: process restart → no re-login needed.

### Concurrent Access Safety

Multiple simultaneous queries must not trigger multiple login requests:

```python
class TokenManager:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_token(self, source_id: str) -> str:
        # Fast path: valid cached token
        cached = await self._load_from_cache(source_id)
        if cached and not self._is_expired(cached):
            return cached.token

        # Slow path: need to refresh/login (with lock)
        async with self._get_lock(source_id):
            # Double-check after acquiring lock
            cached = await self._load_from_cache(source_id)
            if cached and not self._is_expired(cached):
                return cached.token

            return await self._login_and_cache(source_id)

    def _get_lock(self, source_id: str) -> asyncio.Lock:
        if source_id not in self._locks:
            self._locks[source_id] = asyncio.Lock()
        return self._locks[source_id]
```

### Token Injection

After obtaining token, inject into subsequent requests:

| `token_type` config | Injection method |
|---------------------|-----------------|
| `bearer` | `Authorization: Bearer <token>` |
| `header:X-Custom` | `X-Custom: <token>` |
| `query:token` | `?token=<token>` appended to URL |

### Reactive Invalidation (401 Path)

TTL pre-emption covers planned expiry. It cannot cover **server-side
revocation** — a token rotated by policy, invalidated by a concurrent logout,
or rejected early. For production data platforms these are common, and a 401
must trigger re-login rather than a terminal `AUTH_FAILED`.

The HTTP datasource (and every orchestration step that carries auth headers)
wraps each request in a **single-retry reactive interceptor**:

```python
class RequestRunner:
    RETRY_STATUSES = {401}   # configurable per source via auth_failed_statuses

    async def request_with_auth_retry(self, source_id, fn) -> Response:
        token = await self._tokens.get_token(source_id)
        resp = await fn(token)
        if resp.status_code in self.RETRY_STATUSES:
            # Invalidate cache, then re-acquire (Lock-guarded -> one relogin
            # even under concurrency, preserving the acceptance #6 invariant).
            await self._tokens.invalidate(source_id)
            token = await self._tokens.get_token(source_id)
            resp = await fn(token)   # exactly ONE retry
            if resp.status_code in self.RETRY_STATUSES:
                raise DataSourceError(
                    code="AUTH_FAILED",
                    message=f"Auth still rejected after re-login for {source_id}",
                    suggestion="Verify credentials and that the account is not locked/disabled",
                )
        return resp
```

`TokenManager` gains one method:

```python
async def invalidate(self, source_id: str) -> None:
    """Drop the cached token row. Idempotent.

    Releases any in-flight refresh lock for this source so the next
    get_token() re-logs in. Safe to call when no cache exists.
    """
    await self._db.execute("DELETE FROM token_cache WHERE source_id = ?", source_id)
    lock = self._locks.get(source_id)
    if lock and lock.locked():
        # The current lock holder will see the cache miss on its double-check
        # and proceed; callers behind it re-enter the slow path coalesced.
        ...
```

**Invariants preserved**:

| Invariant | How it holds |
|-----------|--------------|
| Single login under concurrency | `invalidate()` only deletes the row; the subsequent `get_token()` still serializes on the per-source Lock with double-check. 5 concurrent 401s → exactly one re-login. |
| Single retry, then fail loud | No backoff loop. A second 401 raises `AUTH_FAILED` immediately — exponential retry policy is the caller's concern, not the ferry's. |
| Not a masking layer | Both the original 401 and the retry are recorded as spans (`auth_retry`, `attributes.attempt=1`) per [[audit-trace]]. Operators see the recovery, not a silent fix. |

**When NOT to retry** (configurable exclusion): non-401 auth failures
(`403 Forbidden`, `407`) are **not** retried — they signal permission problems
that re-login will not fix, and retrying would amplify a misconfigured account's
load. Override via `[sources.X].auth.auth_failed_statuses = [401]` (default).

## Acceptance Criteria (M2)

1. Start mock two-step auth server (provided as test fixture)
2. Configure deepferry to use mock server
3. Agent's first query triggers auto-login (verify: mock login endpoint called exactly once)
4. Agent's second query reuses cached token (verify: mock login endpoint NOT called again)
5. Manually expire token (force TTL), agent silently refreshes before next query
6. 5 concurrent queries: login endpoint called exactly once (concurrent safety verified)
7. Token persists across process restart (SQLite cache survives)
8. A `401` on a data request triggers exactly one re-login + one retry; on success the result is returned (total logins for the session: 2)
9. A second `401` after retry surfaces `AUTH_FAILED` — no third attempt, no backoff loop
10. 5 concurrent requests that all receive `401` trigger exactly **two** logins total (initial + one recovery) — the concurrency invariant survives the reactive path
11. `403 Forbidden` is **not** retried (surfaces immediately) — re-login cannot fix authorization
12. Every 401 retry opens an `auth_retry` span (see [[audit-trace]]) with `attributes.attempt = 1`

## Interview Story

"The core challenge: MCP tools are stateless by design, but two-step auth is inherently stateful. The solution: a token cache layer with asyncio.Lock for concurrent safety. I deliberately used SQLite (not in-memory dict) for token persistence — process restart shouldn't force re-login. This is not an academic innovation; it's production engineering judgment."

## Dependencies

- [[http-api-datasource]] — Consumer of auth tokens
- [[datasource-abstraction]] — Error mapping
