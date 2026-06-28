"""Pydantic models for the orchestration engine.

Defines the declarative schema for multi-step request flows as specified in
``openspec/specs/orchestration.md``: steps, foreach fan-out, and the top-level
``OrchestrationConfig`` built from ``config.toml``.

Backward compatibility
----------------------
The canonical field names follow the spec (``path``, ``body_template``,
``output``, ``id``, ``over``, ``as``).  Legacy field names from the original
monolithic implementation (``url``, ``body``, ``output_key``, ``source_id``,
``array_binding``, ``item_binding``, top-level ``foreach``) are still accepted
via Pydantic ``AliasChoices`` so existing configs and call sites keep working.
The two semantics are bridged inside :class:`OrchestrationEngine`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class StepKind(StrEnum):
    """Role of a step within an orchestrated flow."""

    auth = "auth"
    """Produces a token consumed by later steps."""

    discover = "discover"
    """Produces the dynamic resource list."""

    request = "request"
    """Ordinary data fetch / SQL route."""


class ForeachMode(StrEnum):
    """How per-iteration row sets are combined."""

    merge = "merge"
    """UNION all per-iteration rows into one ``StructuredResult``."""

    separate = "separate"
    """Keep each iteration's result as a distinct dataset."""


class Step(BaseModel):
    """A single HTTP call within an orchestrated flow.

    The canonical field names follow the spec.  Legacy aliases (``url``,
    ``body``, ``output_key``) are accepted for backward compatibility with
    configs written against the original monolithic implementation.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    """Unique name within the flow — used in ``{{step_name.field}}`` bindings."""

    kind: StepKind = StepKind.request
    """Role of the step (auth / discover / request)."""

    method: str = "GET"
    """HTTP method (GET, POST, PUT, PATCH, DELETE)."""

    path: str = Field(
        validation_alias=AliasChoices("path", "url"),
    )
    """URL template — relative paths resolve against ``base_url``; may contain
    ``{{variable}}`` bindings.  Accepts the legacy ``url`` alias."""

    headers: dict[str, str] = Field(default_factory=dict)
    """HTTP headers — values may contain ``{{variable}}`` bindings."""

    body_template: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("body_template", "body"),
    )
    """JSON body template — values may contain ``{{variable}}`` bindings.
    Accepts the legacy ``body`` alias."""

    output: dict[str, str] = Field(default_factory=dict)
    """Alias → JSON-path extractors.  Each entry extracts a value from the
    parsed response and binds it under ``{{step_name.alias}}`` for downstream
    steps.  When empty, the full parsed response is bound under the step name
    (or ``output_key`` when using the legacy form)."""

    foreach: ForeachConfig | None = None
    """Optional per-step foreach fan-out — iterates this step once per array
    element (spec form)."""

    on_error: str = "fail"
    """Legacy error behaviour — ``"fail"`` (default), ``"skip"``, or
    ``"retry"``.  Preserved for backward compatibility."""

    output_key: str | None = None
    """Legacy key in the execution context where the full parsed response is
    stored.  When set, downstream steps reference this via
    ``{{output_key.field}}``.  Ignored when ``output`` is non-empty."""


class ForeachConfig(BaseModel):
    """Configuration for ``foreach`` fan-out over a response array.

    Spec form (per-step): ``over`` / ``as`` / ``mode`` — the owning step is
    executed once per array element.

    Legacy form (top-level on :class:`OrchestrationConfig`): additionally
    carries a nested ``step`` describing the iteration step.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    over: str = Field(
        default="",
        validation_alias=AliasChoices("over", "array_binding"),
    )
    """Binding expression for the array to iterate, e.g.
    ``"discover.instances"``.  Accepts the legacy ``array_binding`` alias."""

    as_: str = Field(
        default="item",
        validation_alias=AliasChoices("as", "item_binding", "as_"),
    )
    """Variable name for each element in the loop context.  Accepts the
    legacy ``item_binding`` alias."""

    mode: ForeachMode = ForeachMode.merge
    """``"merge"`` (UNION) or ``"separate"``.  The legacy value ``"union"``
    is normalised to ``"merge"``."""

    step: Step | None = None
    """Legacy nested iteration step (used by the top-level ``foreach`` form)."""

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_legacy_mode(cls, value: Any) -> Any:
        """Map the legacy ``"union"`` value onto the canonical ``"merge"``."""
        if value == "union":
            return "merge"
        return value

    def is_merge(self) -> bool:
        """Return ``True`` when iterations should be merged (UNIONed)."""
        return self.mode == ForeachMode.merge


class OrchestrationConfig(BaseModel):
    """Declarative configuration for an orchestrated data-source flow.

    Built from ``SourceConfig.extra`` at instantiation time.  The canonical
    identifier field is ``id`` (per spec); the legacy ``source_id`` alias is
    accepted for backward compatibility.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(
        validation_alias=AliasChoices("id", "source_id"),
    )
    """The data source ID (from ``config.toml``).  Accepts the legacy
    ``source_id`` alias."""

    base_url: str | None = None
    """Base URL against which relative step ``path`` values are resolved."""

    steps: list[Step]
    """Ordered list of steps to execute."""

    discovery_ttl_seconds: int = 300
    """Cache TTL (seconds) for discovered resources."""

    foreach: ForeachConfig | None = None
    """Legacy top-level foreach fan-out.  Prefer the per-step ``foreach``
    field on :class:`Step` for new configs."""


# Resolve the Step ⇄ ForeachConfig circular forward references.
Step.model_rebuild()
ForeachConfig.model_rebuild()
