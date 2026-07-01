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

import json
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
class LLMConfig:
    """LLM service configuration parsed from the ``[llm]`` section.

    All string values are ``${ENV_VAR}``-expanded before reaching this dataclass.
    The ``api_key`` **must** use env-var injection — never hardcoded in TOML.
    """

    provider: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    max_tokens: int = 2000
    temperature: float = 0.1
    timeout: int = 15


@dataclass
class StorageConfig:
    """Storage settings parsed from the optional ``[storage]`` section.

    ``data_dir`` is the base directory for dataset files, query caches, and
    other on-disk artifacts.  Defaults to ``~/.deepferry`` when unset.
    """

    data_dir: str = "~/.deepferry"


@dataclass
class AppConfig:
    """The fully-resolved application configuration.

    Produced by ``load_config()`` after parsing ``config.toml`` and expanding
    all ``${ENV_VAR}`` placeholders.
    """

    sources: list[SourceConfig]
    server: ServerConfig = field(default_factory=ServerConfig)
    llm: LLMConfig | None = None
    storage: StorageConfig = field(default_factory=StorageConfig)


# ── Required fields per source type ───────────────────────────────────────

_REQUIRED_FIELDS: dict[str, set[str]] = {
    "mysql": {"host", "port", "database", "user", "password"},
    "postgresql": {"host", "port", "database", "user", "password"},
    "http": {"base_url"},
}


def _validate_source(source: SourceConfig) -> None:
    """Check that a source config has all required fields for its type.

    Custom source types (``type`` starts with ``"custom:"``) perform their own
    validation — they have no universal required fields, so this function is a
    no-op for them.  Built-in types are checked against ``_REQUIRED_FIELDS``.
    """
    # Custom sources own their validation — no universal required fields.
    if source.type.startswith("custom:"):
        return
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

    # ── Parse [llm] section (optional) ─────────────────────────────────────
    llm_raw = raw.get("llm", {})
    llm: LLMConfig | None = None
    if llm_raw:
        llm = LLMConfig(
            provider=str(llm_raw.get("provider", "deepseek")),
            api_key=str(llm_raw.get("api_key", "")),
            model=str(llm_raw.get("model", "deepseek-chat")),
            base_url=str(llm_raw.get("base_url", "https://api.deepseek.com/v1")),
            max_tokens=int(llm_raw.get("max_tokens", 2000)),
            temperature=float(llm_raw.get("temperature", 0.1)),
            timeout=int(llm_raw.get("timeout", 15)),
        )

    # ── Parse [storage] section (optional) ─────────────────────────────────
    storage_raw = raw.get("storage", {})
    storage = StorageConfig(
        data_dir=str(storage_raw.get("data_dir", "~/.deepferry")),
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

    return AppConfig(sources=sources, server=server, llm=llm, storage=storage)


# ── TOML write helpers ───────────────────────────────────────────────────
#
# These functions implement hot-reload-friendly mutation of ``config.toml`` on
# disk.  They deliberately avoid round-tripping through ``tomllib`` (which is
# parse-only and would discard comments, blank lines, and ordering) — instead
# they perform manual text manipulation that preserves the rest of the file
# verbatim.
#
# A ``[[sources]]`` block extends from its header line through any sub-tables
# (``[sources.auth]``, ``[[sources.resources]]``, ``[[sources.steps]]`` …) up
# to the next ``[[sources]]`` header, any other top-level section
# (e.g. ``[server]``), or EOF.


_ID_LINE_RE = re.compile(r'^\s*id\s*=\s*"([^"]*)"')


def _section_path(stripped: str) -> str | None:
    """Return the dotted path of a TOML section header line, else ``None``.

    Examples
    --------
    >>> _section_path("[[sources]]")
    'sources'
    >>> _section_path("[sources.auth]")
    'sources.auth'
    >>> _section_path("[server]")
    'server'
    >>> _section_path("host = \"x\"")
    None
    """
    if stripped.startswith("[[") and stripped.endswith("]]"):
        return stripped[2:-2].strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def _ends_source_block(path: str) -> bool:
    """Whether encountering section *path* terminates the current source block.

    Sub-tables of the current source (``sources.auth``, ``sources.resources``,
    ``sources.steps`` …) do NOT terminate the block — they belong to it.
    Everything else (a new ``sources`` header, ``server``, or any unrelated
    top-level table) does.
    """
    if path == "sources":
        return True
    return not path.startswith("sources.")


def _parse_source_blocks(lines: list[str]) -> list[dict[str, Any]]:
    """Parse a TOML file's lines into ``[[sources]]`` block descriptors.

    Each descriptor is::

        {"id": str | None, "start": int, "end": int}

    where ``start`` is the index of the ``[[sources]]`` header line and
    ``end`` is the exclusive index just past the block's last line (including
    any sub-tables).  ``id`` is extracted from the ``id = "..."`` line; if the
    block is malformed and lacks one, ``id`` is ``None``.
    """
    blocks: list[dict[str, Any]] = []
    current_start: int | None = None
    current_id: str | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        path = _section_path(stripped)
        if path is not None:
            if current_start is not None and _ends_source_block(path):
                blocks.append(
                    {"id": current_id, "start": current_start, "end": i}
                )
                current_start = None
                current_id = None
            if path == "sources":
                current_start = i
                current_id = None
        elif current_start is not None and current_id is None:
            m = _ID_LINE_RE.match(line)
            if m:
                current_id = m.group(1)

    if current_start is not None:
        blocks.append(
            {"id": current_id, "start": current_start, "end": len(lines)}
        )
    return blocks


def _format_toml_value(value: Any) -> str:
    """Serialise a scalar value as a TOML rvalue.

    Supports ``str``, ``int``, ``float``, and ``bool``.  Strings are encoded
    via ``json.dumps`` which produces valid TOML basic strings (double-quoted
    with proper escaping).  Other types raise ``ValueError``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise ValueError(
        f"Cannot serialise value of type {type(value).__name__} to TOML."
    )


def _format_source_block(source: SourceConfig) -> list[str]:
    """Build the list of text lines for a ``[[sources]]`` block.

    The block is emitted with a leading blank line for visual separation and
    contains only non-``None`` top-level fields plus any *scalar* values from
    ``source.extra`` (nested dicts/lists are skipped — they require inline
    table / array-of-tables syntax that is not currently exposed via the
    config CRUD API).
    """
    lines = ["", "[[sources]]"]
    lines.append(f"id = {_format_toml_value(source.id)}")
    lines.append(f"type = {_format_toml_value(source.type)}")
    if source.name is not None:
        lines.append(f"name = {_format_toml_value(source.name)}")
    if source.host is not None:
        lines.append(f"host = {_format_toml_value(source.host)}")
    if source.port is not None:
        lines.append(f"port = {_format_toml_value(source.port)}")
    if source.database is not None:
        lines.append(f"database = {_format_toml_value(source.database)}")
    if source.user is not None:
        lines.append(f"user = {_format_toml_value(source.user)}")
    if source.password is not None:
        lines.append(f"password = {_format_toml_value(source.password)}")
    if source.base_url is not None:
        lines.append(f"base_url = {_format_toml_value(source.base_url)}")
    for key, value in source.extra.items():
        if isinstance(value, str | int | float | bool):
            lines.append(f"{key} = {_format_toml_value(value)}")
    return lines


def _write_lines(path: Path, lines: list[str]) -> None:
    """Join *lines* with newlines and write atomically to *path*."""
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def write_source_to_config(path: Path, source: SourceConfig) -> None:
    """Append a new ``[[sources]]`` block to *path*'s config.toml.

    The block is inserted immediately after the last existing source block so
    that all ``[[sources]]`` entries stay grouped together.  If the file has
    no sources yet, the block is appended at the end of the file.  Existing
    content (including comments and blank lines) is preserved verbatim.

    Parameters
    ----------
    path : Path
        Filesystem path to ``config.toml``.
    source : SourceConfig
        The source definition to write.

    Raises
    ------
    ConfigError
        When the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            code="READ_ERROR",
            message=f"Cannot read {path}: {exc}",
        ) from exc

    lines = text.splitlines()
    blocks = _parse_source_blocks(lines)
    new_block = _format_source_block(source)

    if blocks:
        insert_at = blocks[-1]["end"]
    else:
        # No existing sources — append at end, dropping trailing blank lines.
        insert_at = len(lines)
        while insert_at > 0 and lines[insert_at - 1].strip() == "":
            insert_at -= 1

    new_lines = lines[:insert_at] + new_block + lines[insert_at:]
    _write_lines(path, new_lines)


def update_source_in_config(
    path: Path, source_id: str, source: SourceConfig
) -> None:
    """Replace the ``[[sources]]`` block whose ``id`` matches *source_id*.

    The matched block (including any of its sub-tables) is removed and the
    freshly-formatted block from *source* is written in its place.  All other
    content is preserved verbatim.

    Parameters
    ----------
    path : Path
        Filesystem path to ``config.toml``.
    source_id : str
        The ``id`` of the block to replace.
    source : SourceConfig
        The new source definition.  Its ``id`` field is written verbatim — to
        rename a source, also pass the new ``source_id``.

    Raises
    ------
    ValueError
        When no block with ``id == source_id`` exists.
    ConfigError
        When the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            code="READ_ERROR",
            message=f"Cannot read {path}: {exc}",
        ) from exc

    lines = text.splitlines()
    blocks = _parse_source_blocks(lines)
    target = next((b for b in blocks if b["id"] == source_id), None)
    if target is None:
        raise ValueError(
            f"Source {source_id!r} not found in {path} — cannot update."
        )

    new_block = _format_source_block(source)
    new_lines = lines[: target["start"]] + new_block + lines[target["end"]:]
    _write_lines(path, new_lines)


def remove_source_from_config(path: Path, source_id: str) -> None:
    """Delete the ``[[sources]]`` block whose ``id`` matches *source_id*.

    The matched block (including any of its sub-tables and any immediately
    preceding blank line used for visual separation) is removed.  All other
    content is preserved verbatim.

    Parameters
    ----------
    path : Path
        Filesystem path to ``config.toml``.
    source_id : str
        The ``id`` of the block to remove.

    Raises
    ------
    ValueError
        When no block with ``id == source_id`` exists.
    ConfigError
        When the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            code="READ_ERROR",
            message=f"Cannot read {path}: {exc}",
        ) from exc

    lines = text.splitlines()
    blocks = _parse_source_blocks(lines)
    target = next((b for b in blocks if b["id"] == source_id), None)
    if target is None:
        raise ValueError(
            f"Source {source_id!r} not found in {path} — cannot remove."
        )

    start = target["start"]
    # Also drop a single immediately-preceding blank line (visual separator).
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1

    new_lines = lines[:start] + lines[target["end"]:]
    _write_lines(path, new_lines)
