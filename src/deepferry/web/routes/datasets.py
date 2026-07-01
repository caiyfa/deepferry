"""Dataset CRUD + snapshot/diff/export REST endpoints.

Thin HTTP layer over :class:`DatasetManager`. Not-found error codes map to 404,
``UNSUPPORTED_FORMAT`` to 400; dataclass responses serialise via ``asdict``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from deepferry.core.dataset import CreateDatasetSpec, DatasetManager
from deepferry.core.errors import DeepFerryError
from deepferry.core.models import ColumnMeta, StructuredResult
from deepferry.web.app import get_data_dir

router = APIRouter(prefix="/datasets")

_NOT_FOUND_CODES = {"DATASET_NOT_FOUND", "VERSION_NOT_FOUND", "SNAPSHOT_NOT_FOUND"}
_SUPPORTED_FORMATS = {"parquet", "csv", "json", "arrow"}
_MEDIA_TYPES = {
    "parquet": "application/octet-stream",
    "csv": "text/csv",
    "json": "application/json",
    "arrow": "application/octet-stream",
}


class ColumnMetaRequest(BaseModel):
    """Column descriptor as received over the wire."""

    name: str
    type: str
    nullable: bool = True


class CreateDatasetRequest(BaseModel):
    """Body for dataset creation and snapshot endpoints."""

    name: str
    description: str = ""
    source_query: str
    sources: list[str]
    columns: list[ColumnMetaRequest]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = None
    execution_time_ms: float = 0.0
    source_breakdown: dict[str, dict[str, Any]] | None = None
    refresh_strategy: str = "full"
    incremental_column: str | None = None


def _get_manager(data_dir: str = Depends(get_data_dir)) -> DatasetManager:
    return DatasetManager(data_dir)


def _to_http_error(e: DeepFerryError) -> HTTPException:
    """Map a DeepFerryError onto an HTTPException with a sensible status code."""
    if e.code in _NOT_FOUND_CODES:
        status = 404
    elif e.code == "UNSUPPORTED_FORMAT":
        status = 400
    else:
        status = e.status_code
    return HTTPException(status_code=status, detail=e.to_dict())


def _structured_result_from_columns_rows(
    columns: list[ColumnMetaRequest],
    rows: list[dict[str, Any]],
    row_count: int | None,
    execution_time_ms: float,
) -> StructuredResult:
    column_meta = [ColumnMeta(name=c.name, type=c.type, nullable=c.nullable) for c in columns]
    return StructuredResult(
        columns=column_meta,
        rows=rows,
        row_count=row_count if row_count is not None else len(rows),
        execution_time_ms=execution_time_ms,
    )


def _structured_result_from_payload(payload: dict[str, Any]) -> StructuredResult:
    raw_columns = payload.get("columns", [])
    columns = [ColumnMeta(**c) for c in raw_columns]
    rows = list(payload.get("rows", []))
    return StructuredResult(
        columns=columns,
        rows=rows,
        row_count=payload.get("row_count", len(rows)),
        execution_time_ms=payload.get("execution_time_ms", 0.0),
    )


def _build_spec(req: CreateDatasetRequest) -> CreateDatasetSpec:
    result = _structured_result_from_columns_rows(
        req.columns, req.rows, req.row_count, req.execution_time_ms
    )
    source_breakdown: dict[str, StructuredResult] | None = None
    if req.source_breakdown is not None:
        source_breakdown = {
            sid: _structured_result_from_payload(v)
            for sid, v in req.source_breakdown.items()
        }
    return CreateDatasetSpec(
        name=req.name,
        description=req.description,
        source_query=req.source_query,
        sources=list(req.sources),
        result=result,
        source_breakdown=source_breakdown,
        refresh_strategy=req.refresh_strategy,
        incremental_column=req.incremental_column,
    )


@router.get("")
async def list_datasets(
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, Any]:
    """List all datasets as lightweight summaries."""
    try:
        summaries = await manager.list_datasets()
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "DATASET_LIST_FAILED", "message": str(e)},
        ) from e
    return {"datasets": [asdict(s) for s in summaries]}


@router.post("", status_code=201)
async def create_dataset(
    body: CreateDatasetRequest,
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, Any]:
    """Create a new dataset with its first (``v1``) snapshot."""
    try:
        spec = _build_spec(body)
        dataset = await manager.create(spec)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "DATASET_CREATE_FAILED", "message": str(e)},
        ) from e
    return {"dataset": asdict(dataset)}


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, Any]:
    """Return full dataset metadata, including the version chain."""
    try:
        dataset = await manager.get(dataset_id)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "DATASET_GET_FAILED", "message": str(e)},
        ) from e
    return {"dataset": asdict(dataset)}


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, bool]:
    """Delete a dataset and all of its versions."""
    try:
        await manager.delete(dataset_id)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "DATASET_DELETE_FAILED", "message": str(e)},
        ) from e
    return {"deleted": True}


@router.post("/{dataset_id}/snapshot", status_code=201)
async def create_snapshot(
    dataset_id: str,
    body: CreateDatasetRequest,
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, Any]:
    """Create a new versioned snapshot for an existing dataset."""
    try:
        spec = _build_spec(body)
        snapshot = await manager.create_snapshot(dataset_id, spec)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "SNAPSHOT_CREATE_FAILED", "message": str(e)},
        ) from e
    return {"snapshot": asdict(snapshot)}


@router.get("/{dataset_id}/diff")
async def diff_versions(
    dataset_id: str,
    v1: str = Query(..., min_length=1),
    v2: str = Query(..., min_length=1),
    manager: DatasetManager = Depends(_get_manager),
) -> dict[str, Any]:
    """Return row-level additions/deletions between two versions."""
    try:
        result = await manager.diff(dataset_id, v1, v2)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "DIFF_FAILED", "message": str(e)},
        ) from e
    return {"diff": asdict(result)}


@router.get("/{dataset_id}/export")
async def export_version(
    dataset_id: str,
    format: str = Query(..., min_length=1),
    version: str = Query(..., min_length=1),
    manager: DatasetManager = Depends(_get_manager),
) -> FileResponse:
    """Export a dataset version as a downloadable file."""
    fmt = format.lower().lstrip(".")
    if fmt not in _SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FORMAT",
                "message": f"Format {format!r} is not supported.",
                "suggestion": "Use one of: parquet, csv, json, arrow.",
            },
        )
    try:
        file_path = await manager.export(dataset_id, version, fmt)
    except DeepFerryError as e:
        raise _to_http_error(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "EXPORT_FAILED", "message": str(e)},
        ) from e
    return FileResponse(
        path=str(file_path),
        media_type=_MEDIA_TYPES[fmt],
        filename=f"{dataset_id}_{version}.{fmt}",
    )
