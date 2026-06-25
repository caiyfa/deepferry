"""TOML configuration loading with ``${ENV_VAR}`` injection.

Reads ``config.toml`` at startup and produces a validated ``AppConfig``
object.  All string values are recursively scanned for ``${VAR}`` and
``${VAR:default}`` patterns, which are replaced with the corresponding
environment variable value at load time.

Design decisions
----------------
- Uses ``tomllib`` (Python 3.12 stdlib) — no extra dependency.
- Sync file I/O is acceptable here because config is loaded once at startup
  before the async event loop is running.
- Credentials are never stored in the in-memory config — they come from env
  vars resolved at load time.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepferry.core.errors import ConfigError

# Pattern: ${VAR_NAME} or ${VAR_NAME:default_value}
_ENV_VAR_RE = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


# ── Public API ───────────────────────────────────────────────────────────


def expand_env_vars(value: str) -> str:
    """Replace ``${VAR}`` and ``${VAR:default}`` with environment variable values.

    Parameters
    ----------
    value : str
        A string that may contain zero or more ``${...}`` placeholders.

    Returns
    -------
    str
        The input string with all placeholders resolved.

    Raises
    ------
    ConfigError
        When a referenced environment variable is unset and no default is
        provided.  Code: ``ENV_VAR_UNSET``.
    """
    if not isinstance(value, str):
        raise TypeError(f"expand_env_vars() expects str, got {type(value).__name__}")

    def _replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        raise ConfigError(
            code="ENV_VAR_UNSET",
            message=f"Environment variable {var_name!r} is not set and no default was provided.",
            suggestion=f"Set {var_name} in your environment or add a default: "
            f"${{{var_name}:some_default}}",
        )

    return _ENV_VAR_RE.sub(_replacer, value)


def _expand_env_vars_recursive(obj: Any) -> Any:
    """Recursively walk dicts, lists, and strings, expanding env vars.

    Used internally by ``load_config()`` to process every string value in the
    parsed TOML tree.
    """
    if isinstance(obj, str):
        return expand_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars_recursive(item) for item in obj]
    return obj


# ── Configuration data classes ────────────────────────────────────────────


@dataclass
class ServerConfig:
    """FastAPI / MCP server settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"


@dataclass
class SourceConfig:
    """A single ``[[sources]]`` entry from ``config.toml``.

    Fields correspond to top-level keys in each TOML table.  Any keys not
    listed here are captured in ``extra`` so that source-specific fields
    (e.g. ``headers`` for HTTP sources, ``discovery_ttl_seconds`` for
    orchestrated sources) are available at instantiation time.
    """

    id: str
    type: str
    name: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # Fields that are always moved to ``extra`` rather than becoming top-level attrs.
    _reserved_top_level = frozenset({
        "id", "type", "name", "host", "port",
        "database", "user", "password", "base_url",
    })


@dataclass
class AppConfig:
    """The fully-resolved application configuration.

    Produced by ``load_config()`` after parsing ``config.toml`` and expanding
    all ``${ENV_VAR}`` placeholders.
    """

    sources: list[SourceConfig]
    server: ServerConfig = field(default_factory=ServerConfig)


# ── Required fields per source type ───────────────────────────────────────

_REQUIRED_FIELDS: dict[str, set[str]] = {
    "mysql": {"host", "port", "database", "user", "password"},
    "postgresql": {"host", "port", "database", "user", "password"},
    "http": {"base_url"},
}


def _validate_source(source: SourceConfig) -> None:
    """Check that a source config has all required fields for its type."""
    required = _REQUIRED_FIELDS.get(source.type)
    if required is None:
        # Unknown source type — defer validation to the plugin that handles it.
        return
    missing = required - source.__dict__.keys() - source.extra.keys()
    if missing:
        raise ConfigError(
            code="MISSING_FIELD",
            message=f"Source {source.id!r} (type={source.type!r}) is missing "
            f"required fields: {', '.join(sorted(missing))}",
            suggestion="Add the missing fields to the [[sources]] block in config.toml.",
        )


# ── Main loader ──────────────────────────────────────────────────────────


def load_config(path: str | Path) -> AppConfig:
    """Load and validate ``config.toml``, expanding ``${ENV_VAR}`` references.

    Parameters
    ----------
    path : str | Path
        Filesystem path to ``config.toml``.

    Returns
    -------
    AppConfig
        A validated application configuration with all env vars resolved.

    Raises
    ------
    ConfigError
        When the file is missing, cannot be parsed, or contains invalid values.
    """
    config_path = Path(path)

    if not config_path.is_file():
        raise ConfigError(
            code="MISSING_FILE",
            message=f"Configuration file not found: {config_path}",
            suggestion="Create a config.toml file or provide a valid --config path.",
        )

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            code="READ_ERROR",
            message=f"Cannot read {config_path}: {exc}",
        ) from exc

    try:
        raw = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            code="PARSE_ERROR",
            message=f"Invalid TOML in {config_path}: {exc}",
            suggestion="Check for syntax errors (missing quotes, brackets, etc.).",
        ) from exc

    # Expand env vars in every string value before validation.
    raw = _expand_env_vars_recursive(raw)

    # ── Parse server section (optional) ───────────────────────────────────
    server_raw = raw.get("server", {})
    server = ServerConfig(
        host=str(server_raw.get("host", "127.0.0.1")),
        port=int(server_raw.get("port", 8000)),
        log_level=str(server_raw.get("log_level", "info")),
    )

    # ── Parse [[sources]] blocks ──────────────────────────────────────────
    sources_raw: list[dict[str, Any]] = raw.get("sources", [])
    if not isinstance(sources_raw, list):
        raise ConfigError(
            code="INVALID_SOURCES",
            message="The [[sources]] section must be an array of tables.",
            suggestion="Use [[sources]] (double brackets) for each source block.",
        )

    sources: list[SourceConfig] = []
    for idx, src in enumerate(sources_raw):
        src_id = src.get("id")
        if not src_id:
            raise ConfigError(
                code="MISSING_ID",
                message=f"Source at index {idx} is missing the required 'id' field.",
                suggestion="Every [[sources]] block must have an 'id'.",
            )

        src_type = src.get("type")
        if not src_type:
            raise ConfigError(
                code="MISSING_TYPE",
                message=f"Source {src_id!r} is missing the required 'type' field.",
                suggestion="Set type to one of: mysql, postgresql, http, orchestrated, custom.",
            )

        # Separate known top-level fields from source-specific extras.
        top = {k: src[k] for k in SourceConfig._reserved_top_level & src.keys()}
        extra = {k: v for k, v in src.items() if k not in SourceConfig._reserved_top_level}

        sc = SourceConfig(
            id=src_id,
            type=src_type,
            name=src.get("name"),
            host=top.get("host"),
            port=int(top["port"]) if "port" in top else None,
            database=top.get("database"),
            user=top.get("user"),
            password=top.get("password"),
            base_url=top.get("base_url"),
            extra=extra,
        )
        _validate_source(sc)
        sources.append(sc)

    return AppConfig(sources=sources, server=server)
