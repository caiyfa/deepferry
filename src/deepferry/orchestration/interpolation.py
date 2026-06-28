"""``{{variable}}`` interpolation for orchestration step templates.

A Jinja-subset evaluator that resolves ``{{ ... }}`` tokens against the
accumulated step outputs plus the agent's input.  Only plain dot-path
bindings with optional ``[N]`` index access are supported; Jinja filters,
control-flow tags, and template inheritance are explicitly rejected.

This module is extracted verbatim from the original monolithic
``datasources/orchestrated.py`` — the logic is unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from deepferry.core.errors import DataSourceError

# Matches {{var}}, {{step.field}}, {{step.array[0].field}}, etc.
# Capture group 1: the dotted path (including numeric indices in brackets).
_INTERP_RE = re.compile(r"\{\{(\w+(?:\.\w+)*(?:\[\d+\])?(?:\.\w+)*)\}\}")

# Detects any {{...}} pattern that contains invalid characters (spaces, pipes, %{)
# — these are Jinja-like constructs that must be rejected.
_INVALID_INTERP_RE = re.compile(r"\{\{[^}]*[\s|%#][^}]*\}\}")


def _resolve_path(context: dict[str, Any], path: str) -> Any:
    """Walk *path* (dot-separated with optional ``[N]`` index) through *context*.

    ``path`` is a binding expression like ``"auth.access_token"`` or
    ``"instances[0].id"``.  The function recurses through nested dicts and
    lists until it reaches the terminal value.

    Raises
    ------
    DataSourceError
        With code ``"INVALID_BINDING"`` when any segment cannot be resolved.
    """
    if not path:
        return context

    current: Any = context
    for part in path.split("."):
        if not part:
            continue

        # Handle numeric index suffix, e.g. "instances[0]"
        index: int | None = None
        match = re.search(r"\[(\d+)\]$", part)
        if match:
            index = int(match.group(1))
            part = part[: match.start()]

        if isinstance(current, dict):
            try:
                current = current[part]
            except (KeyError, TypeError) as err:
                raise DataSourceError(
                    code="INVALID_BINDING",
                    message=f"Binding {path!r}: key {part!r} not found in context.",
                    suggestion="Check that the upstream step produced this key.",
                ) from err
        elif isinstance(current, list):
            try:
                i = int(part)
                current = current[i]
            except (ValueError, IndexError) as err:
                raise DataSourceError(
                    code="INVALID_BINDING",
                    message=f"Binding {path!r}: cannot index list with {part!r}.",
                    suggestion="Use a numeric index or a key on a dict element.",
                ) from err
        else:
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Binding {path!r}: cannot traverse {type(current).__name__} "
                f"at segment {part!r}.",
                suggestion="Ensure the binding path matches the output shape.",
            )

        if index is not None:
            try:
                current = current[index]
            except (TypeError, IndexError, KeyError) as err:
                raise DataSourceError(
                    code="INVALID_BINDING",
                    message=f"Binding {path!r}: index [{index}] out of range.",
                    suggestion="Check the array length in the upstream step.",
                ) from err

    return current


def interpolate(template: str, context: dict[str, Any]) -> str:
    """Replace ``{{var}}`` and ``{{step.field}}`` with values from *context*.

    Only supports dotted paths with optional ``[N]`` index access.
    Jinja filters, includes, control-flow tags, and template inheritance are
    **rejected** — if present, they are treated as unresolved bindings.

    Parameters
    ----------
    template : str
        A string that may contain zero or more ``{{...}}`` bindings.
    context : dict
        The accumulated execution context (inputs + step outputs).

    Returns
    -------
    str
        The template with all bindings replaced by their string values.

    Raises
    ------
    DataSourceError
        With code ``"INVALID_BINDING"`` if any binding cannot be resolved.
    """
    if not isinstance(template, str):
        return str(template)

    # Reject Jinja-like constructs (spaces, pipes, control-flow tags)
    if _INVALID_INTERP_RE.search(template):
        raise DataSourceError(
            code="INVALID_BINDING",
            message=f"Template contains an unsupported Jinja-like binding: "
            f"{template!r}.",
            suggestion="Use only plain dot-path bindings like "
            "{{step.field}} or {{step.array[0].field}}.",
        )

    result = template
    for match in _INTERP_RE.finditer(template):
        full_match = match.group(0)
        binding = match.group(1)

        # Reject Jinja extensions — any pipe `|` or brace-opener `{%`
        if "|" in binding or binding.startswith("%") or binding.startswith("#"):
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Binding {full_match!r} uses Jinja filters or control "
                f"flow, which are not supported.",
                suggestion="Use only plain dot-path bindings like "
                "{{step.field}} or {{step.array[0].field}}.",
            )

        try:
            value = _resolve_path(context, binding)
        except DataSourceError:
            raise
        except Exception as exc:
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Binding {full_match!r} could not be resolved: {exc}.",
                suggestion="Check that the referenced step and field are correct.",
            ) from exc

        result = result.replace(full_match, str(value) if value is not None else "", 1)

    return result


def interpolate_dict(
    template: dict[str, Any] | None, context: dict[str, Any]
) -> dict[str, Any] | None:
    """Recursively interpolate ``{{...}}`` bindings in all string values of a dict."""
    if template is None:
        return None
    result: dict[str, Any] = {}
    for key, value in template.items():
        if isinstance(value, str):
            result[key] = interpolate(value, context)
        elif isinstance(value, dict):
            result[key] = interpolate_dict(value, context) or {}
        elif isinstance(value, list):
            result[key] = [
                interpolate(v, context) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value
    return result
