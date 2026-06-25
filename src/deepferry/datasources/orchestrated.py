"""OrchestratedDataSource — multi-step request flows as a single logical DataSource.

Implements the orchestration engine from ``openspec/specs/orchestration.md``.
An orchestrated flow executes a linear sequence of HTTP steps with
``{{variable}}`` interpolation between steps, plus optional ``foreach`` fan-out
over arrays.  From the MCP agent's perspective, the source is indistinguishable
from any other DataSource — one ``query()`` call, one ``StructuredResult``.

Supported topologies
--------------------
- Linear: login → discover → query
- Foreach: discover array → iterate sub-step per element → union or separate

Out of scope: branching, conditionals, while loops, arbitrary DAGs.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, ClassVar

import httpx2
from pydantic import BaseModel, Field

from deepferry.core.errors import ConfigError, DataSourceError
from deepferry.core.models import (
    ColumnMeta,
    HealthStatus,
    QueryRequest,
    Resource,
    ResourceMeta,
    Schema,
    StructuredResult,
)
from deepferry.core.trace import Execution, Span, SpanKind, SpanStatus, TraceSink
from deepferry.datasources.base import DataSource
from deepferry.datasources.registry import register_source_type

if TYPE_CHECKING:
    from deepferry.config import SourceConfig

# ── Interpolation ─────────────────────────────────────────────────────────

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


# ── Pydantic models ─────────────────────────────────────────────────────



class ForeachConfig(BaseModel):
    """Configuration for foreach fan-out over a response array."""

    array_binding: str
    """Binding expression for the array to iterate, e.g. ``"instances"``."""

    item_binding: str = "item"
    """Variable name for each element in the loop context."""

    step: Step
    """The step to execute once per array element."""

    mode: str = "union"
    """"union" to merge all result rows; "separate" to keep datasets distinct."""


class Step(BaseModel):
    """A single HTTP call within an orchestrated flow."""

    name: str
    """Unique name within the flow — used in ``{{step_name.field}}`` bindings."""

    method: str = "GET"
    """HTTP method (GET, POST, PUT, PATCH, DELETE)."""

    url: str
    """URL template — may contain ``{{variable}}`` bindings, resolved at runtime.

    Relative URLs are resolved against ``OrchestrationConfig.base_url``.
    """

    headers: dict[str, str] = Field(default_factory=dict)
    """HTTP headers — values may contain ``{{variable}}`` bindings."""

    body: dict[str, Any] | None = None
    """JSON body template — values may contain ``{{variable}}`` bindings."""

    output_key: str | None = None
    """Key in the execution context where the parsed JSON response is stored.

    Downstream steps reference this via ``{{output_key.field}}``.
    """

    on_error: str = "fail"
    """Error behaviour — ``"fail"`` (stop the flow), ``"skip"`` (continue with
    next step), or ``"retry"`` (retry once)."""


class StepBinding(BaseModel):
    """Declares how a step's output feeds into the query input."""

    step_name: str
    """The step whose output is used as the query input binding."""

    output_key: str
    """The key within that step's output."""


class OrchestrationConfig(BaseModel):
    """Declarative configuration for an orchestrated data-source flow.

    This is built from ``SourceConfig.extra`` at instantiation time.
    """

    source_id: str
    """The data source ID (from ``config.toml``)."""

    base_url: str | None = None
    """Base URL against which relative step ``url`` values are resolved."""

    steps: list[Step]
    """Ordered list of steps to execute."""

    foreach: ForeachConfig | None = None
    """Optional foreach fan-out configuration."""


# ── Cycle detection ─────────────────────────────────────────────────────


def _extract_bindings(value: str) -> set[str]:
    """Return all binding names referenced in *value*."""
    if not isinstance(value, str):
        return set()
    return {match.group(1) for match in _INTERP_RE.finditer(value)}


def _step_dependencies(step: Step) -> set[str]:
    """Return the set of step names that *step* depends on.

    Dependencies are inferred from ``{{step_name.field}}`` bindings in the
    step's ``url``, ``headers`` values, and ``body`` values.
    """
    deps: set[str] = set()
    # Extract bindings from url
    for binding in _extract_bindings(step.url):
        # binding is like "auth.access_token" — first segment is the step name
        deps.add(binding.split(".")[0])
    for header_value in step.headers.values():
        for binding in _extract_bindings(header_value):
            deps.add(binding.split(".")[0])
    if step.body:
        for value in step.body.values():
            if isinstance(value, str):
                for binding in _extract_bindings(value):
                    deps.add(binding.split(".")[0])
    # Also check foreach step if present
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
        # Also check foreach step
        if config.foreach and config.foreach.step.name == step.name:
            foreach_deps = _step_dependencies(config.foreach.step)
            deps |= foreach_deps

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
    """Raise ``ConfigError`` if any step references a step/field that does not exist.

    Checks that every ``{{step_name.field}}`` binding refers to:
    - A defined step name (or the reserved "sql", "params", "item" bindings)
    - OR the output_key of any defined step
    """
    step_names = {step.name for step in config.steps}
    output_key_names: set[str] = {
        step.output_key for step in config.steps if step.output_key
    }

    reserved = {"sql", "params", "item"}
    valid_roots = reserved | step_names | output_key_names

    for step in config.steps:
        all_bindings: set[str] = _extract_bindings(step.url)
        for hv in step.headers.values():
            all_bindings |= _extract_bindings(hv)
        if step.body:
            for v in step.body.values():
                if isinstance(v, str):
                    all_bindings |= _extract_bindings(v)

        for binding in all_bindings:
            root = binding.split(".")[0]
            if root in valid_roots:
                continue
            raise ConfigError(
                code="INVALID_BINDING",
                message=f"Step {step.name!r} references binding "
                f"{{{{{binding}}}}}, but {root!r} is not a known step "
                f"or reserved variable.",
                suggestion=f"Available steps: {sorted(step_names)}. "
                f"Output keys: {sorted(output_key_names)}. "
                f"Reserved variables: {sorted(reserved)}.",
            )

    # Also validate foreach bindings
    if config.foreach:
        fb = config.foreach.array_binding
        root = fb.split(".")[0]
        if root not in valid_roots:
            raise ConfigError(
                code="INVALID_BINDING",
                message=f"Foreach array_binding {fb!r} references unknown "
                f"step or variable {root!r}.",
                suggestion=f"Available steps: {sorted(step_names)}. "
                f"Output keys: {sorted(output_key_names)}.",
            )
        # Validate foreach step's bindings too
        fstep = config.foreach.step
        all_bindings = _extract_bindings(fstep.url)
        for hv in fstep.headers.values():
            all_bindings |= _extract_bindings(hv)
        if fstep.body:
            for v in fstep.body.values():
                if isinstance(v, str):
                    all_bindings |= _extract_bindings(v)
        fb_var = config.foreach.item_binding
        foreach_valid = valid_roots | {fb_var}
        for binding in all_bindings:
            root = binding.split(".")[0]
            if root in foreach_valid:
                continue
            raise ConfigError(
                code="INVALID_BINDING",
                message=f"Foreach step references binding {{{{{binding}}}}}, "
                f"but {root!r} is not a known step, reserved variable, "
                f"or loop variable ({fb_var!r}).",
            )


# ── Orchestration Engine ─────────────────────────────────────────────────


class OrchestrationEngine:
    """Executes a multi-step orchestration flow declared in ``OrchestrationConfig``.

    The engine is stateless — every call to ``run()`` builds a fresh context
    and opens fresh spans.  Concurrent executions are independent.

    Parameters
    ----------
    http_client : httpx2.AsyncClient
        A shared HTTP client used for all step requests.  Base URL should be
        pre-configured from the orchestration config.
    trace_sink : TraceSink | None
        Optional audit-trace sink.  When provided, the engine emits a root
        orchestration span plus one child span per step.
    """

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self._http = http_client
        self._trace = trace_sink

    async def run(
        self, config: OrchestrationConfig, inputs: dict[str, Any]
    ) -> StructuredResult:
        """Execute the full orchestration flow and return the final result."""
        context: dict[str, Any] = {**inputs}
        base = config.base_url or ""

        _detect_cycles(config)
        _detect_undefined_bindings(config)

        root_execution = None
        root_span_id: int | None = None
        if self._trace is not None:
            root_execution = await self._trace.start_execution(config.source_id)
            root_span = Span(
                id=0,
                execution_id=root_execution.id,
                parent_span_id=None,
                span_kind=SpanKind.orchestration,
                span_name=f"orchestrate:{config.source_id}",
                source_id=config.source_id,
                started_at=0,
                attributes={"step_count": len(config.steps)},
            )
            root_span = await self._trace.add_span(root_execution, root_span)
            root_span_id = root_span.id

        try:
            for step in config.steps:
                await self._execute_step(
                    step, context, base, config,
                    root_execution, root_span_id,
                )

            if config.foreach:
                result = await self._run_foreach(
                    config.foreach, context, base, config,
                    root_execution, root_span_id,
                )
            else:
                result = self._build_result_from_context(context, config)

            if self._trace is not None and root_execution is not None:
                await self._trace.finish_execution(root_execution, SpanStatus.ok)

            return result

        except Exception:
            if self._trace is not None and root_execution is not None:
                await self._trace.finish_execution(root_execution, SpanStatus.error)
            raise

    async def _execute_step(
        self,
        step: Step,
        context: dict[str, Any],
        base: str,
        config: OrchestrationConfig,
        root_execution: Execution | None,
        root_span_id: int | None,
    ) -> None:
        url = interpolate(step.url, context)
        if not url.startswith(("http://", "https://")):
            url = base.rstrip("/") + "/" + url.lstrip("/")
        headers = {
            k: interpolate(v, context)
            for k, v in step.headers.items()
        }
        body = interpolate_dict(step.body, context)

        step_span = None
        if self._trace is not None and root_execution is not None:
            step_span = Span(
                id=0,
                execution_id=root_execution.id,
                parent_span_id=root_span_id,
                span_kind=SpanKind.http_request,
                span_name=step.name,
                source_id=config.source_id,
                started_at=0,
                attributes={
                    "url": url,
                    "method": step.method,
                    "step_name": step.name,
                },
            )

        start = time.perf_counter()
        step_status = SpanStatus.ok
        try:
            response = await self._http.request(
                method=step.method,
                url=url,
                headers=headers,
                json=body,
                follow_redirects=True,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Check for HTTP errors
            if response.status_code >= 400:
                step_status = SpanStatus.error
                if step.on_error == "fail":
                    raise DataSourceError(
                        code="STEP_FAILED",
                        message=f"Step {step.name!r} failed with HTTP {response.status_code}: "
                        f"{response.text[:500]}",
                        suggestion=f"Step URL: {url}. Check credentials, permissions, "
                        f"or the response from the upstream step.",
                    )
                elif step.on_error == "skip":
                    context[step.output_key or step.name] = None
                    return
                # "retry" — retry once
                elif step.on_error == "retry":
                    try:
                        response = await self._http.request(
                            method=step.method,
                            url=url,
                            headers=headers,
                            json=body,
                            follow_redirects=True,
                        )
                        if response.status_code >= 400:
                            raise DataSourceError(
                                code="STEP_FAILED",
                                message=f"Step {step.name!r} failed with HTTP "
                                f"{response.status_code} after retry: "
                                f"{response.text[:500]}",
                                suggestion=f"Step URL: {url}.",
                            )
                    except DataSourceError:
                        raise
                    except Exception as exc:
                        raise DataSourceError(
                            code="STEP_FAILED",
                            message=f"Step {step.name!r} failed on retry: {exc}.",
                            suggestion=f"Step URL: {url}.",
                        ) from exc

            # Parse JSON response
            try:
                data = response.json()
            except Exception:
                data = response.text

            # Store output
            output_key = step.output_key or step.name
            context[output_key] = data

            if self._trace is not None and step_span is not None:
                step_span.attributes["http_status"] = response.status_code
                step_span.attributes["elapsed_ms"] = round(elapsed_ms, 2)

        except DataSourceError:
            step_status = SpanStatus.error
            raise
        except httpx2.HTTPError as exc:
            step_status = SpanStatus.error
            raise DataSourceError(
                code="STEP_FAILED",
                message=f"Step {step.name!r} HTTP error: {exc}.",
                suggestion=f"Step URL: {url}. Check network connectivity and "
                f"the upstream service.",
            ) from exc
        finally:
            if self._trace is not None and step_span is not None and root_execution is not None:
                await self._trace.add_span(root_execution, step_span)
                await self._trace.finish_span(step_span, step_status)

    async def _run_foreach(
        self,
        foreach: ForeachConfig,
        context: dict[str, Any],
        base: str,
        config: OrchestrationConfig,
        root_execution: Execution | None,
        root_span_id: int | None,
    ) -> StructuredResult:
        """Fan out over an array and execute the foreach step per element.

        Parameters
        ----------
        foreach : ForeachConfig
            The foreach configuration (array binding, loop var, mode, sub-step).
        context : dict
            The accumulated execution context.
        base : str
            Base URL for relative step URLs.
        config : OrchestrationConfig
            The full orchestration config (for source_id, etc.).
        root_span : Span | None
            The root orchestration span (for parent linkage).

        Returns
        -------
        StructuredResult
            Merged result in "union" mode; the last element's result in "separate"
            mode (multi-dataset metadata).

        Raises
        ------
        DataSourceError
            With ``FOREACH_SCHEMA_MISMATCH`` when union columns diverge.
        """
        array = _resolve_path(context, foreach.array_binding)
        if not isinstance(array, list):
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Foreach array_binding {foreach.array_binding!r} resolved to "
                f"{type(array).__name__}, expected a list.",
                suggestion="Ensure the upstream step produces an array.",
            )

        all_rows: list[dict[str, Any]] = []
        all_columns: list[ColumnMeta] | None = None
        separate_results: list[StructuredResult] = []

        for idx, element in enumerate(array):
            # Create per-iteration context
            iter_context = {**context}
            iter_context[foreach.item_binding] = element

            step = foreach.step

            # Interpolate
            url = interpolate(step.url, iter_context)
            if not url.startswith(("http://", "https://")):
                url = base.rstrip("/") + "/" + url.lstrip("/")
            headers = {
                k: interpolate(v, iter_context)
                for k, v in step.headers.items()
            }
            body = interpolate_dict(step.body, iter_context)

            # Open foreach child span
            fspan = None
            if self._trace is not None and root_execution is not None:
                fspan = Span(
                    id=0,
                    execution_id=root_execution.id,
                    parent_span_id=root_span_id,
                    span_kind=SpanKind.http_request,
                    span_name=f"{step.name}[{idx}]",
                    source_id=config.source_id,
                    started_at=0,
                    attributes={
                        "url": url,
                        "method": step.method,
                        "foreach_index": idx,
                        "foreach_item": foreach.item_binding,
                    },
                )

            fstatus = SpanStatus.ok
            try:
                response = await self._http.request(
                    method=step.method,
                    url=url,
                    headers=headers,
                    json=body,
                    follow_redirects=True,
                )

                if response.status_code >= 400:
                    fstatus = SpanStatus.error
                    raise DataSourceError(
                        code="STEP_FAILED",
                        message=f"Foreach iteration {idx} (step {step.name!r}) failed "
                        f"with HTTP {response.status_code}: {response.text[:500]}",
                        suggestion=f"URL: {url}. Earlier iterations may have "
                        f"succeeded.",
                    )

                data = response.json()

                # Extract rows from response
                rows = self._extract_rows(data)
                columns = self._infer_columns(rows)

                if foreach.mode == "union":
                    if all_columns is None:
                        all_columns = columns
                    else:
                        # Check schema match
                        existing_names = {c.name for c in all_columns}
                        new_names = {c.name for c in columns}
                        if existing_names != new_names:
                            raise DataSourceError(
                                code="FOREACH_SCHEMA_MISMATCH",
                                message=f"Foreach iteration {idx} produced columns "
                                f"{sorted(new_names)}, but previous iterations "
                                f"had {sorted(existing_names)}.",
                                suggestion="Use 'separate' mode for heterogeneous "
                                "schemas.",
                            )
                    all_rows.extend(rows)
                else:
                    # separate mode
                    separate_results.append(
                        StructuredResult(
                            columns=columns,
                            rows=rows,
                            row_count=len(rows),
                            execution_time_ms=0,
                        )
                    )

            except DataSourceError:
                fstatus = SpanStatus.error
                raise
            except Exception as exc:
                fstatus = SpanStatus.error
                raise DataSourceError(
                    code="STEP_FAILED",
                    message=f"Foreach iteration {idx} failed: {exc}.",
                ) from exc
            finally:
                if self._trace is not None and fspan is not None and root_execution is not None:
                    await self._trace.add_span(root_execution, fspan)
                    await self._trace.finish_span(fspan, fstatus)

        if foreach.mode == "union":
            return StructuredResult(
                columns=all_columns or [],
                rows=all_rows,
                row_count=len(all_rows),
                execution_time_ms=0,
            )
        else:
            # separate mode: return the last result's metadata
            # (multi-dataset info is stored in result metadata for inspection)
            if not separate_results:
                return StructuredResult(
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=0,
                )
            return separate_results[-1]

    @staticmethod
    def _extract_rows(data: Any) -> list[dict[str, Any]]:
        """Extract a list of rows from a JSON response.

        Handles root arrays and common wrapper objects like ``{"data": [...]}``.
        """
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "items", "results", "records", "rows"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
            # Fallback: wrap the dict as a single row
            return [data]
        return []

    @staticmethod
    def _infer_columns(rows: list[dict[str, Any]]) -> list[ColumnMeta]:
        """Infer column metadata from a list of row dicts."""
        if not rows:
            return []
        columns: list[ColumnMeta] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    py_type = type(row[key]).__name__
                    sql_type = {
                        "int": "integer",
                        "float": "float",
                        "bool": "boolean",
                        "str": "text",
                        "NoneType": "text",
                    }.get(py_type, "text")
                    columns.append(
                        ColumnMeta(name=key, type=sql_type, nullable=True)
                    )
        return columns

    def _build_result_from_context(
        self, context: dict[str, Any], config: OrchestrationConfig
    ) -> StructuredResult:
        """Build a ``StructuredResult`` from the last step's output in context."""
        # The "result" is the output of the last step
        last_step = config.steps[-1]
        output_key = last_step.output_key or last_step.name
        data = context.get(output_key)
        if data is None:
            return StructuredResult(
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=0,
            )
        rows = self._extract_rows(data)
        columns = self._infer_columns(rows)
        return StructuredResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=0,
        )


# ── OrchestratedDataSource ──────────────────────────────────────────────


class OrchestratedDataSource(DataSource):
    """A ``DataSource`` backed by a multi-step request flow.

    Implements all six ABC methods.  From the MCP agent's perspective, an
    orchestrated source is indistinguishable from any other source — one
    ``query()`` call, one ``StructuredResult``.

    The orchestration flow is declared in ``config.toml`` via
    ``[[sources.steps]]`` blocks, which land in ``SourceConfig.extra["steps"]``.
    """

    source_type: ClassVar[str] = "orchestrated"

    def __init__(
        self,
        config: SourceConfig,
        http_client: httpx2.AsyncClient | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._http_client = http_client
        self._owns_client = http_client is None
        self._engine: OrchestrationEngine | None = None
        self._trace = trace_sink
        self._discovered: list[Resource] | None = None
        self._discovery_ts: float = 0.0
        self._discovery_ttl: int = config.extra.get("discovery_ttl_seconds", 300)

        # Build the orchestration config eagerly for validation
        self._orchestration_config = self._build_config()

    # ── Config building ─────────────────────────────────────────────────

    def _build_config(self) -> OrchestrationConfig:
        """Build an ``OrchestrationConfig`` from ``SourceConfig.extra``.

        Called at ``__init__`` time so that validation errors (cycle detection,
        undefined bindings) are raised at startup, not at query time.
        """
        steps_raw = self._config.extra.get("steps", [])
        if not steps_raw:
            raise ConfigError(
                code="MISSING_FIELD",
                message=f"Orchestrated source {self._config.id!r} has no steps defined.",
                suggestion="Add [[sources.steps]] blocks in config.toml.",
            )

        steps: list[Step] = []
        for raw in steps_raw:
            steps.append(Step(**raw))

        foreach_config: ForeachConfig | None = None
        foreach_raw = self._config.extra.get("foreach")
        if foreach_raw:
            foreach_config = ForeachConfig(**foreach_raw)

        config = OrchestrationConfig(
            source_id=self._config.id,
            base_url=self._config.base_url or self._config.extra.get("base_url"),
            steps=steps,
            foreach=foreach_config,
        )

        # Validate eagerly
        _detect_cycles(config)
        _detect_undefined_bindings(config)

        return config

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the HTTP client if one was not injected.

        Idempotent — calling on an already-connected source is a no-op.
        """
        if self._engine is not None:
            return
        if self._http_client is None:
            self._http_client = httpx2.AsyncClient(
                base_url=self._config.base_url or "",
                timeout=30.0,
            )
            self._owns_client = True
        self._engine = OrchestrationEngine(self._http_client, self._trace)

    async def disconnect(self) -> None:
        """Close the HTTP client if owned by this source.

        Idempotent — safe to call multiple times.
        """
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        self._engine = None

    # ── Query execution ─────────────────────────────────────────────────

    async def execute(self, query: QueryRequest) -> StructuredResult:
        """Execute the orchestration flow and return the final result.

        *query.statement* is bound as ``{{sql}}`` in step templates.
        *query.params* are bound as ``{{params}}``.

        Parameters
        ----------
        query : QueryRequest
            The query to execute, with SQL in ``statement``.

        Returns
        -------
        StructuredResult
            The merged result from the orchestration flow.

        Raises
        ------
        DataSourceError
            With codes ``STEP_FAILED``, ``INVALID_BINDING``, or
            ``FOREACH_SCHEMA_MISMATCH``.
        """
        if self._engine is None:
            raise DataSourceError(
                code="NOT_CONNECTED",
                message=f"Orchestrated source {self._config.id!r} is not connected.",
                suggestion="Call connect() before executing queries.",
            )

        inputs: dict[str, Any] = {
            "sql": query.statement,
        }
        if query.params:
            inputs["params"] = query.params

        return await self._engine.run(self._orchestration_config, inputs)

    # ── Resource discovery ──────────────────────────────────────────────

    async def list_resources(self) -> list[Resource]:
        """Return the dynamic resource list discovered at runtime.

        If no discovery has occurred or the cache TTL has expired, runs the
        flow up to and including the first step whose ``output_key`` yields
        an array of resource-like objects.

        Returns
        -------
        list[Resource]
            The dynamically-discovered resources.
        """
        # Static resources from config (fallback)
        static_resources_raw = self._config.extra.get("resources", [])
        if static_resources_raw:
            return [Resource(**r) for r in static_resources_raw]

        # Dynamic discovery
        if self._discovered is not None and time.time() - self._discovery_ts < self._discovery_ttl:
            return self._discovered

        # Run discovery: execute flow up to (and including) the first step
        # whose output is a list
        if self._engine is None:
            return []

        base = self._config.base_url or ""
        config = self._orchestration_config
        context: dict[str, Any] = {}

        for step in config.steps:
            await self._engine._execute_step(step, context, base, config, None, None)

            output_key = step.output_key or step.name
            data = context.get(output_key)
            if isinstance(data, list):
                resources: list[Resource] = []
                for item in data:
                    name = item.get("id") or item.get("name") or str(item)
                    rtype = item.get("type", "endpoint")
                    desc = item.get("description")
                    resources.append(Resource(name=name, type=rtype, description=desc))
                self._discovered = resources
                self._discovery_ts = time.time()
                return resources

        # No discovery step found — return what we have
        return self._discovered or []

    # ── Schema info ─────────────────────────────────────────────────────

    async def schema_info(self, resource: str | None = None) -> Schema:
        """Return column-level schema metadata.

        Samples a query with ``LIMIT 1`` to infer schema from the response,
        since orchestrated sources don't have static schema introspection.

        Parameters
        ----------
        resource : str | None
            Name of the resource to introspect.  When ``None``, returns the
            schema for all discovered resources.

        Returns
        -------
        Schema
            One ``ResourceMeta`` per resource with inferred columns.
        """
        if self._engine is None:
            return Schema(resources=[])

        # Try to run a minimal query to infer schema
        try:
            result = await self.execute(
                QueryRequest(
                    source_id=self._config.id,
                    statement="SELECT 1",
                    timeout=10,
                )
            )
            return Schema(resources=[
                ResourceMeta(
                    name=resource or "default",
                    columns=result.columns,
                )
            ])
        except DataSourceError:
            return Schema(resources=[])

    # ── Health check ────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Check connectivity by probing the base URL.

        Returns
        -------
        HealthStatus
            ``ok`` is ``True`` when the base URL responds within the timeout.
        """
        if self._http_client is None:
            try:
                client = httpx2.AsyncClient(timeout=10.0)
                start = time.perf_counter()
                resp = await client.get(self._config.base_url or "/")
                elapsed_ms = (time.perf_counter() - start) * 1000
                await client.aclose()
                return HealthStatus(
                    ok=resp.status_code < 500,
                    latency_ms=round(elapsed_ms, 2),
                    error=None if resp.status_code < 500 else f"HTTP {resp.status_code}",
                )
            except Exception as exc:
                return HealthStatus(
                    ok=False,
                    latency_ms=0,
                    error=str(exc),
                )

        start = time.perf_counter()
        try:
            resp = await self._http_client.get(
                self._config.base_url or "/",
                timeout=10.0,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            return HealthStatus(
                ok=resp.status_code < 500,
                latency_ms=round(elapsed_ms, 2),
                error=None if resp.status_code < 500 else f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return HealthStatus(
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )


# ── Registration ─────────────────────────────────────────────────────────

register_source_type("orchestrated", OrchestratedDataSource)
