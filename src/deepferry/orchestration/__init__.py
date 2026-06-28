"""deepferry orchestration package — multi-step request flow execution.

Implements the orchestration engine from ``openspec/specs/orchestration.md``.
An orchestrated flow executes a linear sequence of HTTP steps with
``{{variable}}`` interpolation between steps, plus optional ``foreach``
fan-out over arrays.

Public symbols
--------------
- :class:`OrchestrationConfig`, :class:`Step`, :class:`ForeachConfig` —
  declarative flow models.
- :class:`StepKind`, :class:`ForeachMode` — step / foreach enumerations.
- :class:`OrchestrationEngine` — the stateless flow executor.
- :func:`validate_orchestration_config` — eager config validation.
- :func:`interpolate`, :func:`interpolate_dict` — template interpolation.

The package is extracted from the original monolithic
``datasources/orchestrated.py``; behaviour is preserved.
"""

from deepferry.orchestration.engine import OrchestrationEngine
from deepferry.orchestration.interpolation import interpolate, interpolate_dict
from deepferry.orchestration.models import (
    ForeachConfig,
    ForeachMode,
    OrchestrationConfig,
    Step,
    StepKind,
)
from deepferry.orchestration.validation import (
    _detect_cycles,
    _detect_undefined_bindings,
    validate_orchestration_config,
)

__all__ = [
    "ForeachConfig",
    "ForeachMode",
    "OrchestrationConfig",
    "OrchestrationEngine",
    "Step",
    "StepKind",
    "_detect_cycles",
    "_detect_undefined_bindings",
    "interpolate",
    "interpolate_dict",
    "validate_orchestration_config",
]
