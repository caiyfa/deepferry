"""Static validation for orchestration configs.

Two checks run eagerly at config-build time (see
:func:`validate_orchestration_config`):

1. **Cycle detection** — every step's bindings may only reference steps that
   appear *earlier* in the flow.  Only linear topologies are supported.
2. **Undefined-binding detection** — every ``{{binding}}`` must reference a
   known step name, a declared ``output_key``/``output`` alias, or one of the
   reserved variables (``sql``, ``params``, ``item``).

Both checks raise :class:`~deepferry.core.errors.ConfigError` so that invalid
configs are rejected at startup, never at query time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deepferry.core.errors import ConfigError
from deepferry.orchestration.interpolation import _INTERP_RE

if TYPE_CHECKING:
    from deepferry.orchestration.models import OrchestrationConfig, Step

# Reserved binding roots that are always valid (injected by the engine).
_RESERVED_BINDINGS: frozenset[str] = frozenset({"sql", "params", "item"})


def _extract_bindings(value: Any) -> set[str]:
    """Return all binding names referenced in *value*.

    Non-string values contribute no bindings.  The matcher is the
    interpolation regex from :mod:`deepferry.orchestration.interpolation`.
    """
    if not isinstance(value, str):
        return set()
    return {match.group(1) for match in _INTERP_RE.finditer(value)}


def _step_template_bindings(step: Step) -> set[str]:
    """Collect every ``{{binding}}`` referenced in a step's request template.

    Scans ``path``, ``headers`` values, and ``body_template`` values.
    """
    bindings: set[str] = _extract_bindings(step.path)
    for header_value in step.headers.values():
        bindings |= _extract_bindings(header_value)
    if step.body_template:
        for value in step.body_template.values():
            if isinstance(value, str):
                bindings |= _extract_bindings(value)
    return bindings


def _step_dependencies(step: Step) -> set[str]:
    """Return the set of step names that *step* depends on.

    Dependencies are inferred from ``{{step_name.field}}`` bindings in the
    step's ``path``, ``headers``, ``body_template``, and (when present) the
    per-step ``foreach.over`` reference.
    """
    deps: set[str] = set()
    for binding in _step_template_bindings(step):
        # binding is like "auth.access_token" — first segment is the step name
        deps.add(binding.split(".")[0])
    if step.foreach and step.foreach.over:
        deps.add(step.foreach.over.split(".")[0])
    return deps


def _detect_cycles(config: OrchestrationConfig) -> None:
    """Raise ``ConfigError`` if any step depends on a later step (cycle).

    Uses a simple topological check: for each step at index *i*, all its
    dependencies must appear at indices < i.  This enforces strict linear
    ordering — the only supported topology.
    """
    step_names = {step.name for step in config.steps}
    name_to_index: dict[str, int] = {}
    for i, step in enumerate(config.steps):
        if step.name in name_to_index:
            raise ConfigError(
                code="DUPLICATE_STEP_NAME",
                message=f"Step name {step.name!r} appears more than once.",
                suggestion="Each step must have a unique name.",
            )
        name_to_index[step.name] = i

    for i, step in enumerate(config.steps):
        deps = _step_dependencies(step)
        # Legacy top-level foreach: its nested step carries its own deps.
        if (
            config.foreach
            and config.foreach.step is not None
            and config.foreach.step.name == step.name
        ):
            deps |= _step_dependencies(config.foreach.step)

        for dep in deps:
            # Filter out non-step bindings (e.g. "sql", "params", "item")
            if dep not in step_names:
                continue
            dep_idx = name_to_index[dep]
            if dep_idx >= i:
                raise ConfigError(
                    code="CIRCULAR_DEPENDENCY",
                    message=f"Step {step.name!r} depends on step {dep!r}, "
                    f"which appears at or after position {dep_idx} (step "
                    f"position {i}).",
                    suggestion="Reorder steps so that dependencies come before "
                    "the steps that reference them.  Only linear topologies "
                    "are supported.",
                )


def _detect_undefined_bindings(config: OrchestrationConfig) -> None:
    """Raise ``ConfigError`` if any step references an unknown step/field.

    Checks that every ``{{step_name.field}}`` binding refers to:
    - a defined step name (or the reserved "sql", "params", "item" bindings)
    - OR a declared ``output_key`` of any step

    Also validates ``foreach.over`` references for both the per-step and
    legacy top-level foreach forms.
    """
    step_names = {step.name for step in config.steps}
    output_key_names: set[str] = {
        step.output_key for step in config.steps if step.output_key
    }

    valid_roots = _RESERVED_BINDINGS | step_names | output_key_names

    def _check_step(step: Step, extra_roots: frozenset[str] = frozenset()) -> None:
        allowed = valid_roots | extra_roots
        for binding in _step_template_bindings(step):
            root = binding.split(".")[0]
            if root in allowed:
                continue
            raise ConfigError(
                code="INVALID_BINDING",
                message=f"Step {step.name!r} references binding "
                f"{{{{{binding}}}}}, but {root!r} is not a known step "
                f"or reserved variable.",
                suggestion=f"Available steps: {sorted(step_names)}. "
                f"Output keys: {sorted(output_key_names)}. "
                f"Reserved variables: {sorted(_RESERVED_BINDINGS)}.",
            )
        # Per-step foreach: the ``over`` array must resolve to a prior step,
        # and the loop variable must be usable inside the step template.
        if step.foreach:
            over_root = step.foreach.over.split(".")[0] if step.foreach.over else ""
            if over_root and over_root not in valid_roots:
                raise ConfigError(
                    code="INVALID_BINDING",
                    message=f"Step {step.name!r} foreach.over "
                    f"{step.foreach.over!r} references unknown step or "
                    f"variable {over_root!r}.",
                    suggestion=f"Available steps: {sorted(step_names)}. "
                    f"Output keys: {sorted(output_key_names)}.",
                )

    for step in config.steps:
        _check_step(step)

    # Legacy top-level foreach validation.
    if config.foreach:
        fb = config.foreach.over
        if fb:
            root = fb.split(".")[0]
            if root not in valid_roots:
                raise ConfigError(
                    code="INVALID_BINDING",
                    message=f"Foreach array_binding {fb!r} references unknown "
                    f"step or variable {root!r}.",
                    suggestion=f"Available steps: {sorted(step_names)}. "
                    f"Output keys: {sorted(output_key_names)}.",
                )
        fstep = config.foreach.step
        if fstep is not None:
            loop_var = config.foreach.as_
            _check_step(fstep, extra_roots=frozenset({loop_var}))


def validate_orchestration_config(config: OrchestrationConfig) -> None:
    """Run all static validation checks against *config*.

    Raises
    ------
    ConfigError
        With code ``CIRCULAR_DEPENDENCY``, ``DUPLICATE_STEP_NAME``, or
        ``INVALID_BINDING`` when the config is malformed.  Called eagerly at
        :class:`~deepferry.datasources.orchestrated.OrchestratedDataSource`
        construction time so invalid configs never reach query time.
    """
    _detect_cycles(config)
    _detect_undefined_bindings(config)
