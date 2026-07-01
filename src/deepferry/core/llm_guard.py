"""Shared SQL safety validation for LLM-generated SQL.

Datasource-agnostic: validates SQL before we know which source it targets.
The :func:`scan_sql` function catches stacked queries and dangerous keywords
regardless of dialect.
"""

from __future__ import annotations

import re

from .errors import DataSourceError

# ── Constants ──────────────────────────────────────────────────────────────

_DANGEROUS_KEYWORDS = frozenset({
    "DROP",
    "TRUNCATE",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "GRANT",
    "REPLACE",
    "MERGE",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "CALL",
})

_ALLOWED_PREFIXES = frozenset({
    "SELECT",
    "SHOW",
    "DESCRIBE",
    "EXPLAIN",
    "WITH",
})


# ── Public API ─────────────────────────────────────────────────────────────


def scan_sql(sql: str) -> None:
    """Validate LLM-generated SQL for safety before execution.

    Performs three checks in order:

    1. **Empty check** — rejects blank or whitespace-only input.
    2. **Prefix check** — rejects statements that do not start with an
       allowed keyword (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH).
    3. **Keyword scan** — splits on ``;`` to catch stacked queries, then
       checks each segment for dangerous keywords using ``\\bWORD\\b``
       word-boundary matching so column names like ``updated_at`` are
       not misclassified as writes.

    Args:
        sql: The raw SQL string (may contain leading / trailing whitespace).

    Raises:
        DataSourceError: ``code="INVALID_SQL"`` when the SQL is empty or does
            not start with an allowed prefix.
        DataSourceError: ``code="WRITE_BLOCKED"`` when a dangerous keyword is
            detected in any segment.
    """
    stripped = sql.strip()

    # 1. Empty / whitespace-only
    if not stripped:
        raise DataSourceError(
            code="INVALID_SQL",
            message="SQL statement is empty or whitespace-only.",
            suggestion="Provide a valid SQL query starting with SELECT, SHOW, DESCRIBE, EXPLAIN, or WITH.",
        )

    upper = stripped.upper()

    # 2. Dangerous keyword scan (word-boundary, split on ;).
    #    Runs before the prefix check so standalone statements like
    #    'DROP TABLE x' raise WRITE_BLOCKED (not INVALID_SQL).
    for segment in upper.split(";"):
        for kw in _DANGEROUS_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", segment):
                raise DataSourceError(
                    code="WRITE_BLOCKED",
                    message=f"Dangerous keyword '{kw}' detected in SQL statement.",
                    suggestion="Only SELECT queries are allowed. Use SHOW/DESCRIBE/EXPLAIN for schema exploration.",
                )

    # 3. Must start with an allowed keyword (case-insensitive)
    if not any(upper.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        raise DataSourceError(
            code="INVALID_SQL",
            message="SQL must start with SELECT, SHOW, DESCRIBE, EXPLAIN, or WITH.",
            suggestion="Only read-only exploration queries are permitted. Use SHOW/DESCRIBE/EXPLAIN for schema exploration.",
        )
