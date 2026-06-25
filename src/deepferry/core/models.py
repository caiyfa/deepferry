"""Shared Pydantic v2 models for the deepferry data access layer.

All models here define the canonical shapes for data flowing between the MCP
server, datasource implementations, and the configuration panel.  Concrete
datasources produce these models; the MCP server consumes them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Column & resource metadata ────────────────────────────────────────


class ColumnMeta(BaseModel):
    """Metadata for a single column in a table, view, or endpoint result."""

    name: str
    type: str
    nullable: bool = True


class Resource(BaseModel):
    """A queryable resource — table, view, or API endpoint."""

    name: str
    type: str  # "table", "view", "endpoint"
    description: str | None = None


class ResourceMeta(BaseModel):
    """Detailed metadata for a single resource including its columns."""

    name: str
    columns: list[ColumnMeta]


class Schema(BaseModel):
    """Full schema of a data source — one ResourceMeta per resource."""

    resources: list[ResourceMeta]


class TableInfo(BaseModel):
    """Lightweight resource descriptor returned by the list_tables MCP tool."""

    name: str
    type: str  # "table", "view", "endpoint"
    row_count: int | None = None


# ── Health & discovery ─────────────────────────────────────────────────


class HealthStatus(BaseModel):
    """Result of a datasource health check."""

    ok: bool
    latency_ms: float
    error: str | None = None


class SourceSummary(BaseModel):
    """Summary of a configured data source exposed via list_sources."""

    id: str
    name: str
    type: str
    health: str  # "healthy", "unhealthy", "unknown"


# ── Query request & result ─────────────────────────────────────────────


class QueryRequest(BaseModel):
    """A query to execute against a specific data source."""

    source_id: str
    statement: str
    params: dict[str, Any] | None = None
    timeout: int = Field(default=30, ge=1, le=300)
    max_rows: int | None = Field(default=None, ge=1)


class StructuredResult(BaseModel):
    """Result of a query execution — columns + rows + metadata."""

    columns: list[ColumnMeta]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float


class QueryResult(BaseModel):
    """Result returned by the query MCP tool — extends StructuredResult with source_id."""

    columns: list[ColumnMeta]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float
    source_id: str
