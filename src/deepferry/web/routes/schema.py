"""GET /schema/{source_id} — introspect a data source's schema.

Returns a ``Schema { resources: ResourceMeta[] }`` with column-level metadata
for every resource.  When ``?table=<name>`` is given, the result is filtered to
that single resource.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from deepferry.core.errors import SourceNotFoundError
from deepferry.core.models import Schema  # noqa: TC001  # used as return type annotation
from deepferry.datasources.registry import (
    SourceRegistry,  # noqa: TC001  # used as parameter annotation
)
from deepferry.web.app import get_registry

router = APIRouter(prefix="")


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
