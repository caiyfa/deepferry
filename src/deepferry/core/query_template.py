"""``{{param}}`` template parser for saved queries.

A focused, standalone parser that extracts parameter names from SQL templates
and renders them by substituting values from a parameter dict.  This is
simpler than the orchestration interpolation module — saved-query params are
plain names, not dotted paths or Jinja expressions.
"""

from __future__ import annotations

import re
from typing import Any

_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_params(template: str) -> list[str]:
    """Return the ordered, de-duplicated list of ``{{param}}`` names in *template*.

    >>> extract_params("SELECT * FROM t WHERE x = {{x}} AND y = {{y}}")
    ['x', 'y']
    >>> extract_params("SELECT {{x}}, {{x}}")  # de-duplicated
    ['x']
    """
    names: list[str] = []
    seen: set[str] = set()
    for match in _PARAM_RE.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def render(template: str, params: dict[str, Any]) -> str:
    """Substitute ``{{param}}`` bindings from *params*.

    Substitution rules (SQL-safe):
    * Every ``{{name}}`` is looked up in *params*.
    * If *name* is **present**:
      - ``str``           → ``'`` + value.replace("'", "''") + ``'``
      - ``int`` / ``float`` → ``str(value)``
      - ``bool``          → ``TRUE`` / ``FALSE``
      - ``None``          → ``NULL``
    * If *name* is **missing** from *params* the ``{{name}}`` placeholder is
      left verbatim so that callers can detect incomplete substitution.

    >>> render("WHERE x = {{x}}", {"x": 42})
    'WHERE x = 42'
    >>> render("WHERE s = {{s}}", {"s": "it's"})
    "WHERE s = 'it''s'"
    >>> render("WHERE b = {{b}}", {"b": True})
    'WHERE b = TRUE'
    >>> render("WHERE n = {{n}}", {"n": None})
    'WHERE n = NULL'
    >>> render("WHERE f = {{f}}", {"f": 3.14})
    'WHERE f = 3.14'
    >>> render("WHERE a = {{a}}", {})
    'WHERE a = {{a}}'
    """
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            return match.group(0)  # leave placeholder verbatim
        value = params[name]
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        # Fallback for other types — stringify and quote
        return f"'{str(value)}'"

    return _PARAM_RE.sub(_replace, template)


def validate_params(template: str, provided: dict[str, Any]) -> list[str]:
    """Return the list of required params that are MISSING from *provided*.

    >>> validate_params("SELECT {{x}}, {{y}}", {"x": 1})
    ['y']
    >>> validate_params("SELECT {{x}}, {{y}}", {"x": 1, "y": 2})
    []
    """
    required = extract_params(template)
    return [name for name in required if name not in provided]
