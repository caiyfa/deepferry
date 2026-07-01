"""Schema introspection endpoints.

``GET /schema/relationships`` returns cross-source column pairs that look
JOINable, detected by matching column names (case-insensitive) across different
sources with compatible types.

``GET /schema/{source_id}`` returns a ``Schema { resources: ResourceMeta[] }``
with column-level metadata for every resource of a single data source.  When
``?table=<name>`` is given, the result is filtered to that single resource.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from deepferry.core.errors import SourceNotFoundError
from deepferry.core.models import Schema  # noqa: TC001  # used as return type annotation
from deepferry.datasources.registry import (
    SourceRegistry,  # noqa: TC001  # used as parameter annotation
)
from deepferry.web.app import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# ── Type compatibility helpers ──────────────────────────────────────────

_TYPE_FAMILIES: list[tuple[frozenset[str]]] = [
    (frozenset({"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "SERIAL", "BIGSERIAL"}),),
    (frozenset({"VARCHAR", "TEXT", "CHAR", "STRING", "CITEXT"}),),
    (frozenset({"DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ"}),),
    (frozenset({"BOOLEAN", "BOOL"}),),
    (frozenset({"DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"}),),
]


def _types_compatible(a: str, b: str) -> bool:
    ua, ub = a.upper(), b.upper()
    if ua == ub:
        return True
    return any(ua in family and ub in family for family, in _TYPE_FAMILIES)


def _is_fk_like(col_name: str) -> bool:
    name = col_name.lower().strip()
    return name == "id" or name.endswith("_id")


# ── Non-parameterised routes first (otherwise /schema/{source_id} catches everything) ──


@router.get("/schema/relationships")
async def get_relationships(
    registry: SourceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return cross-source column pairs that look JOINable.

    Detected by matching column names (case-insensitive) across DIFFERENT
    sources with compatible types.  Each candidate includes a ``confidence``
    score (0.0–1.0) based on name match, type compatibility, and foreign-key
    heuristics.  Results are de-duplicated (each unordered pair appears once),
    sorted by confidence DESC, and limited to the top 100.
    """
    summaries = registry.list_sources()
    if len(summaries) < 2:
        return []

    idx: dict[str, list[tuple[str, str, str, str]]] = {}

    for summary in summaries:
        src_id = summary.id
        try:
            source = registry.get(src_id)
            schema: Schema = await source.schema_info()
        except Exception:
            logger.warning("Failed to introspect schema for source %r, skipping.", src_id, exc_info=True)
            continue

        for resource in schema.resources:
            for col in resource.columns:
                norm = col.name.lower().strip()
                idx.setdefault(norm, []).append((src_id, resource.name, col.name, col.type))

    if len(idx) < 1:
        return []

    seen: set[tuple[tuple[str, str, str], tuple[str, str, str]]] = set()
    candidates: list[dict[str, Any]] = []

    for _norm_name, entries in idx.items():
        src_ids = {e[0] for e in entries}
        if len(src_ids) < 2:
            continue

        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a_src, a_res, a_col, a_type = entries[i]
                b_src, b_res, b_col, b_type = entries[j]

                if a_src == b_src:
                    continue

                left_key = (a_src, a_res, a_col)
                right_key = (b_src, b_res, b_col)
                if left_key > right_key:
                    left_key, right_key = right_key, left_key

                pair = (left_key, right_key)
                if pair in seen:
                    continue
                seen.add(pair)

                confidence: float = 0.5
                if _types_compatible(a_type, b_type):
                    confidence += 0.3
                if _is_fk_like(a_col) or _is_fk_like(b_col):
                    confidence += 0.2
                confidence = min(confidence, 1.0)

                candidates.append({
                    "left": {"source_id": left_key[0], "resource": left_key[1], "column": left_key[2]},
                    "right": {"source_id": right_key[0], "resource": right_key[1], "column": right_key[2]},
                    "confidence": confidence,
                })

    candidates.sort(
        key=lambda c: (
            -c["confidence"],
            c["left"]["source_id"],
            c["left"]["resource"],
            c["left"]["column"],
            c["right"]["source_id"],
            c["right"]["resource"],
            c["right"]["column"],
        ),
    )

    return candidates[:100]


# ── Parameterised routes ────────────────────────────────────────────────


@router.get("/schema/{source_id}")
async def get_schema(
    source_id: str,
    table: str | None = None,
    registry: SourceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Return schema metadata for a data source.

    Missing sources yield a structured 404; any other failure yields a
    structured 500.
    """
    try:
        source = registry.get(source_id)
        schema: Schema = await source.schema_info(resource=table)
        return schema.model_dump(mode="json")
    except SourceNotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict()) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "SCHEMA_FAILED",
                "message": str(e),
            },
        ) from e
