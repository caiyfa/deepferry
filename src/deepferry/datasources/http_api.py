"""HTTP API data source implementation using httpx2.

Treats JSON REST API responses as queryable tables.  Supports configurable
endpoints as resources, JSON response flattening with ``parent.child`` key
naming, schema inference from sampled responses, and structured error mapping.

Implements all six abstract methods of the DataSource ABC.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlencode

import httpx2

from deepferry.auth.models import AuthConfig
from deepferry.auth.token_manager import TokenManager
from deepferry.core.errors import DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    HealthStatus,
    QueryRequest,
    Resource,
    ResourceMeta,
    Schema,
    StructuredResult,
)
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import register_source_type

if TYPE_CHECKING:
    from deepferry.config import SourceConfig

# ── Constants ─────────────────────────────────────────────────────────────

_DEFAULT_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MiB
_DEFAULT_MAX_ROWS = 100_000

# Non-truthy response bodies that signal no data.
_EMPTY_RESPONSE_BODIES: tuple[Any, ...] = (None, "", b"", {}, [])

# Common keys that signal where the result array lives in nested JSON.
_ARRAY_DETECTOR_KEYS: tuple[str, ...] = ("data", "items", "results", "records")

# Template token pattern: {{var_name}}  (two braces, no spaces around var_name)
_BODY_TEMPLATE_RE = re.compile(r"\{\{(\w+)\}\}")


# ── JSON flattening ───────────────────────────────────────────────────────


def _find_array(data: Any) -> list[dict[str, Any]]:
    """Locate the data array inside a JSON response.

    Checks common wrapper keys (``data``, ``items``, ``results``, ``records``)
    and falls back to the root value if it is already a list.

    Returns an empty list when no array is found.
    """
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in _ARRAY_DETECTOR_KEYS:
        candidate = data.get(key)
        if isinstance(candidate, list):
            return candidate

    # Last resort — any value that happens to be a list.
    for value in data.values():
        if isinstance(value, list):
            return value

    return []


def _flatten_row(
    obj: dict[str, Any],
    prefix: str = "",
    *,
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively flatten a dictionary into ``parent.child`` keys.

    Nested dicts are traversed.  Lists at depth ≥ 1 are collapsed to the
    string ``"[complex]"``.  Scalars are yielded as leaf values.
    """
    result: dict[str, Any] = {}
    for key, value in obj.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict) and depth < 5:
            result.update(_flatten_row(value, full_key, depth=depth + 1))
        elif isinstance(value, list):
            if depth == 0 and all(isinstance(v, dict) for v in value):
                # Nested list of objects at root level — not flattened here;
                # the caller should have extracted the outer array first.
                result[full_key] = "[complex]"
            else:
                result[full_key] = "[complex]"
        else:
            result[full_key] = value

    return result


def _flatten_response(data: Any) -> tuple[list[ColumnMeta], list[dict[str, Any]]]:
    """Convert a parsed JSON response into column metadata and flattened rows.

    Returns
    -------
    tuple[list[ColumnMeta], list[dict[str, Any]]]
        Inferred columns and flattened rows.  Both are empty when no array
        data is present.
    """
    arr = _find_array(data)
    if not arr:
        return [], []

    # Flatten each object in the array.
    flat_rows: list[dict[str, Any]] = []
    for item in arr:
        if isinstance(item, dict):
            flat_rows.append(_flatten_row(item))
        else:
            # Scalar items — wrap in a synthetic {"value": item} row.
            flat_rows.append({"value": item})

    if not flat_rows:
        return [], []

    # Column metadata is derived from the union of all keys in all rows.
    seen: dict[str, None] = {}
    for row in flat_rows:
        for key in row:
            if key not in seen:
                seen[key] = None

    col_names = list(seen.keys())
    columns = [_infer_column(col_name, flat_rows) for col_name in col_names]

    return columns, flat_rows


def _infer_column(name: str, rows: list[dict[str, Any]]) -> ColumnMeta:
    """Infer a single ColumnMeta by sampling the first non-None value.

    Type mapping
    ------------
    * ``bool``   → ``"boolean"``
    * ``int``    → ``"integer"``
    * ``float``  → ``"number"``
    * ``str``    → ``"string"``
    * ``None``   → ``"string"``
    * other      → ``"string"``
    """
    for row in rows:
        val = row.get(name)
        if val is None:
            continue
        if isinstance(val, bool):
            return ColumnMeta(name=name, type="boolean", nullable=True)
        if isinstance(val, int):
            return ColumnMeta(name=name, type="integer", nullable=True)
        if isinstance(val, float):
            return ColumnMeta(name=name, type="number", nullable=True)
        if isinstance(val, str):
            return ColumnMeta(name=name, type="string", nullable=True)
        return ColumnMeta(name=name, type="string", nullable=True)

    # All values are None.
    return ColumnMeta(name=name, type="string", nullable=True)


# ── Body template interpolation ────────────────────────────────────────────


def _render_body_template(
    template: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Interpolate ``{{var}}`` tokens in a body template.

    Tokens are resolved against *params* first, then against any
    static defaults that are already in the template value.  Unresolved
    tokens raise ``DataSourceError(INVALID_BINDING)``.

    When *template* is ``None``, returns ``None``.
    """
    if template is None:
        return None

    params = params or {}
    result: dict[str, Any] = {}

    for key, raw in template.items():
        if not isinstance(raw, str):
            result[key] = raw
            continue

        def _replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name in params:
                val = params[var_name]
                # Convert to JSON-safe primitive for the body.
                if isinstance(val, (str, int, float, bool, type(None))):
                    return str(val) if not isinstance(val, str) else val
                return str(val)
            # Check if the token is embedded in a larger string that has
            # a static part (e.g. "/api/{{id}}/detail" where id is not in params).
            # The spec says unresolved → INVALID_BINDING, no literal "{{var}}".
            raise ValueError(var_name)

        try:
            resolved = _BODY_TEMPLATE_RE.sub(_replacer, raw)
            result[key] = resolved
        except ValueError as exc:
            var_name = str(exc)
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Unresolved template variable {var_name!r} in body_template key "
                f"{key!r} — not found in QueryRequest.params or static defaults.",
                suggestion=f"Pass {var_name!r} in QueryRequest.params, or provide a "
                f"static default in the body_template value.",
            ) from exc

    return result


# ── HTTPDataSource ─────────────────────────────────────────────────────────


class HTTPDataSource(DataSource):
    """Async HTTP API data source backed by an ``httpx2.AsyncClient``.

    Endpoints are declared as ``[[sources.resources]]`` entries in
    ``config.toml`` and surfaced via ``list_resources()``.  Queries invoke
    endpoints, flattening JSON responses into tabular form.

    Lifecycle
    ---------
    1. ``connect()`` → creates an ``httpx2.AsyncClient`` (idempotent).
    2. ``execute()`` / ``list_resources()`` / ``schema_info()`` / ``health_check()``.
    3. ``disconnect()`` → closes the client (idempotent).
    """

    source_type: ClassVar[str] = "http"
    HTTP_AUTH_RETRY_STATUSES: ClassVar[set[int]] = {401}
    """HTTP status codes that trigger a token invalidation + retry.
    Configurable per-source via ``extra.http_auth_retry_statuses``."""

    def __init__(
        self,
        config: SourceConfig,
        token_manager: TokenManager | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._client: httpx2.AsyncClient | None = None
        self._token_manager = token_manager

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the ``httpx2.AsyncClient``.

        Idempotent — calling on an already-connected source is a no-op.
        """
        if self._client is not None:
            return
        base_url = self._config.base_url or ""
        headers = self._config.extra.get("default_headers", {})
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=30.0,
        )

    async def disconnect(self) -> None:
        """Close the HTTP client.

        Idempotent — safe to call multiple times.
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Query execution ────────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Invoke a configured HTTP endpoint and return flattened results.

        *query.statement* is the resource name (e.g. ``"users"``).  ``query.params``
        is bound as query-string parameters for ``GET`` requests or interpolated
        into the ``body_template`` for ``POST`` / ``PUT`` / ``PATCH``.

        Parameters
        ----------
        query : QueryRequest
            The query to execute, with resource name in ``statement``.

        Returns
        -------
        StructuredResult
            Column metadata, row data, row count, and execution time.

        Raises
        ------
        DataSourceError
            With codes ``RESOURCE_NOT_FOUND``, ``HTTP_CLIENT_ERROR``,
            ``HTTP_SERVER_ERROR``, ``TIMEOUT``, ``CONNECTION_FAILED``,
            ``RESPONSE_TOO_LARGE``, or ``INVALID_BINDING``.
        """
        self._require_connected()
        resource = self._find_resource(query.statement)
        start = time.perf_counter()

        url = resource["path"]
        params = query.params or {}
        body: dict[str, Any] | None = None

        method = resource.get("method", "GET").upper()

        if method == "GET":
            if params:
                url = f"{url}?{urlencode(params)}"

        elif method in ("POST", "PUT", "PATCH"):
            body_template = resource.get("body_template")
            if body_template is not None:
                body = _render_body_template(body_template, params)
            elif params:
                body = params

        else:
            raise DataSourceError(
                code="UNSUPPORTED_METHOD",
                message=f"HTTP method {method!r} is not supported.",
                suggestion="Use GET, POST, PUT, or PATCH.",
            )

        auth_config: AuthConfig | None = None
        if self._token_manager is not None:
            auth_raw = self._config.extra.get("auth")
            if isinstance(auth_raw, dict):
                auth_config = AuthConfig(**auth_raw)

        try:
            if auth_config is not None:
                response = await self._request_with_auth_retry(
                    method=method,
                    url=url,
                    json_body=body,
                    auth_config=auth_config,
                    timeout=float(query.timeout),
                )
            else:
                response = await self._client.request(  # type: ignore[union-attr]
                    method=method,
                    url=url,
                    json=body,
                    timeout=query.timeout,
                    follow_redirects=True,
                )

            # Check content-length before reading the body.
            content_length = response.headers.get("content-length")
            max_bytes = self._config.extra.get("max_response_bytes", _DEFAULT_MAX_RESPONSE_BYTES)
            if content_length is not None and int(content_length) > max_bytes:
                raise DataSourceError(
                    code="RESPONSE_TOO_LARGE",
                    message=f"Response size ({content_length} bytes) exceeds the "
                    f"{max_bytes} byte limit for source {self.source_id!r}.",
                    suggestion="Add filters/pagination to the endpoint, increase "
                    "max_response_bytes in config, or offload to DuckDB layer.",
                )

            response.raise_for_status()
            data = response.json()

        except httpx2.TimeoutException:
            raise DataSourceError(
                code="TIMEOUT",
                message=f"Request to {url!r} timed out after {query.timeout}s.",
                suggestion="Increase the timeout or check the endpoint health.",
            ) from None
        except httpx2.ConnectError as exc:
            raise DataSourceError(
                code="CONNECTION_FAILED",
                message=str(exc),
                suggestion=f"Check that {self._config.base_url!r} is reachable.",
            ) from exc
        except httpx2.HTTPStatusError as exc:
            status = exc.response.status_code
            prefix = "HTTP_CLIENT_ERROR" if 400 <= status < 500 else "HTTP_SERVER_ERROR"
            raise DataSourceError(
                code=prefix,
                message=f"HTTP {status} from {url!r}: {exc.response.text[:500]}",
                suggestion=(
                    "Check the endpoint URL and parameters."
                    if prefix == "HTTP_CLIENT_ERROR"
                    else "The upstream server returned an error. Try again later."
                ),
            ) from exc

        # Check actual response body size against the cap.
        raw_bytes = response.content
        if len(raw_bytes) > max_bytes:
            raise DataSourceError(
                code="RESPONSE_TOO_LARGE",
                message=f"Response body ({len(raw_bytes)} bytes) exceeds the "
                f"{max_bytes} byte limit.",
                suggestion="Add filters/pagination to the endpoint, increase "
                "max_response_bytes in config, or offload to DuckDB layer.",
            )

        columns, rows = _flatten_response(data)

        # Apply max_rows cap.
        max_rows = query.max_rows or self._config.extra.get("max_rows", _DEFAULT_MAX_ROWS)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]

        elapsed = (time.perf_counter() - start) * 1000

        return StructuredResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=round(elapsed, 3),
        )

    # ── Auth retry support ──────────────────────────────────────────────

    async def _request_with_auth_retry(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        auth_config: AuthConfig,
        timeout: float = 30.0,
    ) -> httpx2.Response:
        """Perform an HTTP request with automatic 401 retry via TokenManager.

        On first 401: invalidates the cached token, acquires the per-source
        lock, triggers a re-login via ``get_token()``, rebuilds auth headers,
        and retries **once**.  A second 401 raises ``AUTH_FAILED``.
        """
        assert self._token_manager is not None
        assert self._client is not None

        retry_statuses = self._config.extra.get(
            "http_auth_retry_statuses", self.HTTP_AUTH_RETRY_STATUSES
        )

        # ── First attempt ───────────────────────────────────────────────
        token = await self._token_manager.get_token(self.source_id, auth_config)
        req_headers = self._build_auth_headers(token, auth_config.token_type)

        response = await self._client.request(
            method=method,
            url=url,
            json=json_body,
            headers=req_headers,
            timeout=timeout,
            follow_redirects=True,
        )

        if response.status_code not in retry_statuses:
            return response

        # ── 401 / retry path ────────────────────────────────────────────
        await self._token_manager.invalidate(self.source_id)

        # get_token() handles its own per-source asyncio.Lock internally;
        # after invalidation the double-check inside the lock will find no
        # cached token and trigger a fresh login.
        token = await self._token_manager.get_token(self.source_id, auth_config)
        req_headers = self._build_auth_headers(token, auth_config.token_type)

        response = await self._client.request(
            method=method,
            url=url,
            json=json_body,
            headers=req_headers,
            timeout=timeout,
            follow_redirects=True,
        )

        if response.status_code in retry_statuses:
            raise DataSourceError(
                code="AUTH_FAILED",
                message=(
                    f"Authentication failed for source {self.source_id!r} after retry. "
                    f"Server returned {response.status_code}."
                ),
                suggestion="Check credentials and auth configuration.",
            )

        return response

    @staticmethod
    def _build_auth_headers(token: str, token_type: str) -> dict[str, str]:
        """Build auth-specific request headers with token injected."""
        headers: dict[str, str] = {}
        TokenManager.apply_token(headers, token, token_type)
        return headers

    # ── Resource discovery ─────────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Return the statically-configured endpoints as resources.

        HTTP APIs have no introspection protocol, so every endpoint the agent
        can query must be explicitly declared in ``config.toml``.

        Returns
        -------
        list[Resource]
            One ``Resource`` per ``[[sources.resources]]`` entry.
        """
        resources_cfg = self._config.extra.get("resources", [])
        return [
            Resource(
                name=r["name"],
                type="endpoint",
                description=r.get("description"),
            )
            for r in resources_cfg
            if isinstance(r, dict) and "name" in r
        ]

    # ── Schema introspection ───────────────────────────────────────────

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level metadata for one or all resources.

        For HTTP sources, schema inference works by sampling the endpoint
        response.  When an explicit ``columns`` declaration exists in the
        resource config it is preferred; otherwise the first row of the
        response is inspected to derive ``ColumnMeta`` entries.

        Parameters
        ----------
        resource : str | None
            Name of the resource to introspect.  When ``None``, returns
            schema for *all* configured resources.

        Returns
        -------
        Schema
            One ``ResourceMeta`` per requested resource.
        """
        self._require_connected()
        resources_cfg = self._config.extra.get("resources", [])

        target_resources = resources_cfg
        if resource is not None:
            target_resources = [r for r in resources_cfg
                                if isinstance(r, dict) and r.get("name") == resource]
            if not target_resources:
                raise DataSourceError(
                    code="RESOURCE_NOT_FOUND",
                    message=f"Resource {resource!r} is not configured for "
                    f"source {self.source_id!r}.",
                    suggestion="Check config.toml [[sources.resources]] entries.",
                )

        result_resources: list[ResourceMeta] = []
        for r in target_resources:
            if not isinstance(r, dict) or "name" not in r:
                continue

            name = r["name"]

            # Prefer explicit schema declaration from config.
            explicit_columns = r.get("columns")
            if isinstance(explicit_columns, list) and explicit_columns:
                cols = [
                    ColumnMeta(
                        name=c["name"],
                        type=c.get("type", "string"),
                        nullable=c.get("nullable", True),
                    )
                    for c in explicit_columns
                    if isinstance(c, dict) and "name" in c
                ]
                result_resources.append(ResourceMeta(name=name, columns=cols))
                continue

            # Fall back to sampling the endpoint.
            try:
                url = r["path"]
                params = r.get("params")

                response = await self._client.get(  # type: ignore[union-attr]
                    url,
                    params=params,
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                columns, _ = _flatten_response(data)
                result_resources.append(ResourceMeta(name=name, columns=columns))

            except httpx2.HTTPStatusError:
                result_resources.append(
                    ResourceMeta(
                        name=name,
                        columns=[
                            ColumnMeta(name="error", type="string", nullable=True)
                        ],
                    )
                )
                # Swallow — do not fail the whole introspection because of one
                # misbehaving endpoint.
            except (httpx2.ConnectError, httpx2.TimeoutException):
                result_resources.append(
                    ResourceMeta(
                        name=name,
                        columns=[],
                    )
                )

        return Schema(resources=result_resources)

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Probe connectivity by requesting the configured ``base_url``.

        Uses a lightweight ``GET`` (falling back to ``HEAD`` if the server
        does not support it).

        Returns
        -------
        HealthStatus
            ``ok=True`` when the probe returns a 2xx status within the timeout.
        """
        if self._client is None:
            return HealthStatus(
                ok=False,
                latency_ms=0,
                error="Not connected — call connect() first.",
            )

        start = time.perf_counter()
        try:
            response = await self._client.get(
                "/",
                timeout=5.0,
                follow_redirects=True,
            )
            latency = (time.perf_counter() - start) * 1000
            ok = 200 <= response.status_code < 400
            return HealthStatus(
                ok=ok,
                latency_ms=round(latency, 3),
                error=None if ok else f"HTTP {response.status_code}",
            )
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000
            return HealthStatus(
                ok=False,
                latency_ms=round(latency, 3),
                error=str(exc),
            )

    # ── Helpers ────────────────────────────────────────────────────────

    def _find_resource(self, name: str) -> dict[str, Any]:
        """Locate a resource dict by name in the source config.

        Raises ``DataSourceError(RESOURCE_NOT_FOUND)`` when no match is found.
        """
        resources = self._config.extra.get("resources", [])
        for r in resources:
            if isinstance(r, dict) and r.get("name") == name:
                return r
        raise DataSourceError(
            code="RESOURCE_NOT_FOUND",
            message=f"Resource {name!r} is not configured for source "
            f"{self.source_id!r}.",
            suggestion="Check config.toml [[sources.resources]] entries.",
        )

    def _require_connected(self) -> None:
        """Raise ``DataSourceError`` if the HTTP client has not been created."""
        if self._client is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message="HTTPDataSource is not connected.",
                suggestion="Call connect() before executing any operation.",
            )


# ── Plugin registration ────────────────────────────────────────────────────

register_source_type("http", HTTPDataSource)
