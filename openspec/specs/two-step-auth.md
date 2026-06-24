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

## Acceptance Criteria (M2)

1. Start mock two-step auth server (provided as test fixture)
2. Configure deepferry to use mock server
3. Agent's first query triggers auto-login (verify: mock login endpoint called exactly once)
4. Agent's second query reuses cached token (verify: mock login endpoint NOT called again)
5. Manually expire token (force TTL), agent silently refreshes before next query
6. 5 concurrent queries: login endpoint called exactly once (concurrent safety verified)
7. Token persists across process restart (SQLite cache survives)

## Interview Story

"The core challenge: MCP tools are stateless by design, but two-step auth is inherently stateful. The solution: a token cache layer with asyncio.Lock for concurrent safety. I deliberately used SQLite (not in-memory dict) for token persistence — process restart shouldn't force re-login. This is not an academic innovation; it's production engineering judgment."

## Dependencies

- [[http-api-datasource]] — Consumer of auth tokens
- [[datasource-abstraction]] — Error mapping
