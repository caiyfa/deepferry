"""OrchestrationEngine — executes multi-step request flows.

The engine is stateless: every call to :meth:`OrchestrationEngine.run` builds a
fresh context and opens fresh spans.  Concurrent executions are independent.

Supported topologies (hard cap — see ``openspec/specs/orchestration.md``):

* **Linear** — ``login → discover → query``.
* **Foreach** — iterate a step once per element of a bound array, then merge
  (UNION) or keep separate.  Two declaration forms are supported:

  - **Per-step** (spec form): ``Step.foreach`` declares fan-out inline; the
    owning step is executed once per element.
  - **Top-level** (legacy form): ``OrchestrationConfig.foreach`` carries a
    nested ``step``; executed after all linear steps.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx2

from deepferry.core.errors import DataSourceError
from deepferry.core.models import ColumnMeta, StructuredResult
from deepferry.core.trace import Execution, Span, SpanKind, SpanStatus, TraceSink
from deepferry.orchestration.interpolation import (
    _resolve_path,
    interpolate,
    interpolate_dict,
)
from deepferry.orchestration.validation import validate_orchestration_config

if TYPE_CHECKING:
    from deepferry.orchestration.models import (
        ForeachConfig,
        OrchestrationConfig,
        Step,
    )


_EMPTY_RESULT = StructuredResult(
    columns=[], rows=[], row_count=0, execution_time_ms=0
)


class OrchestrationEngine:
    """Executes a multi-step orchestration flow declared in an
    :class:`~deepferry.orchestration.models.OrchestrationConfig`.

    Parameters
    ----------
    http_client : httpx2.AsyncClient
        A shared HTTP client used for all step requests.  Relative step paths
        are resolved against ``config.base_url``.
    trace_sink : TraceSink | None
        Optional audit-trace sink.  When provided, the engine emits a root
        orchestration span plus one child span per step (and per foreach
        iteration).
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
        """Execute the full orchestration flow and return the final result.

        Parameters
        ----------
        config : OrchestrationConfig
            The declarative flow definition.
        inputs : dict
            Agent-supplied bindings — ``sql`` and optional ``params``.

        Returns
        -------
        StructuredResult
            The merged result from the final step or foreach fan-out.
        """
        context: dict[str, Any] = {**inputs}
        base = config.base_url or ""

        # Defensive re-validation (also runs eagerly at config-build time).
        validate_orchestration_config(config)

        root_execution: Execution | None = None
        root_span_id: int | None = None
        if self._trace is not None:
            root_execution = await self._trace.start_execution(config.id)
            root_span = Span(
                id=0,
                execution_id=root_execution.id,
                parent_span_id=None,
                span_kind=SpanKind.orchestration,
                span_name=f"orchestrate:{config.id}",
                source_id=config.id,
                started_at=0,
                attributes={"step_count": len(config.steps)},
            )
            root_span = await self._trace.add_span(root_execution, root_span)
            root_span_id = root_span.id

        try:
            result: StructuredResult = _EMPTY_RESULT
            for step in config.steps:
                if step.foreach is not None:
                    # Per-step (spec-form) foreach: iterate the step itself.
                    result = await self._foreach_loop(
                        step, step.foreach, context, base, config,
                        root_execution, root_span_id,
                    )
                else:
                    await self._execute_step(
                        step, context, base, config,
                        root_execution, root_span_id,
                    )
                    result = self._build_result_from_context(context, config)

            # Legacy top-level foreach (runs after all linear steps).
            if config.foreach is not None:
                result = await self._run_foreach(
                    config.foreach, context, base, config,
                    root_execution, root_span_id,
                )

            if self._trace is not None and root_execution is not None:
                await self._trace.finish_execution(root_execution, SpanStatus.ok)

            return result

        except Exception:
            if self._trace is not None and root_execution is not None:
                await self._trace.finish_execution(root_execution, SpanStatus.error)
            raise

    # ── Linear step execution ────────────────────────────────────────────

    async def _execute_step(
        self,
        step: Step,
        context: dict[str, Any],
        base: str,
        config: OrchestrationConfig,
        root_execution: Execution | None,
        root_span_id: int | None,
    ) -> None:
        """Execute a single linear HTTP step and store its output in *context*."""
        url = interpolate(step.path, context)
        if not url.startswith(("http://", "https://")):
            url = base.rstrip("/") + "/" + url.lstrip("/")
        headers = {
            k: interpolate(v, context)
            for k, v in step.headers.items()
        }
        body = interpolate_dict(step.body_template, context)

        step_span: Span | None = None
        if self._trace is not None and root_execution is not None:
            step_span = Span(
                id=0,
                execution_id=root_execution.id,
                parent_span_id=root_span_id,
                span_kind=SpanKind.http_request,
                span_name=step.name,
                source_id=config.id,
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
                    self._store_output(step, context, None)
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
                data: Any = response.json()
            except Exception:
                data = response.text

            # Store output
            self._store_output(step, context, data)

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

    # ── Foreach fan-out ──────────────────────────────────────────────────

    async def _run_foreach(
        self,
        foreach: ForeachConfig,
        context: dict[str, Any],
        base: str,
        config: OrchestrationConfig,
        root_execution: Execution | None,
        root_span_id: int | None,
    ) -> StructuredResult:
        """Legacy top-level foreach — iterates the nested ``foreach.step``."""
        if foreach.step is None:
            raise DataSourceError(
                code="INVALID_CONFIG",
                message="Top-level foreach declared without a nested 'step'.",
                suggestion="Use the per-step 'foreach' field on a Step, or "
                "supply a 'step' on the top-level foreach config.",
            )
        return await self._foreach_loop(
            foreach.step, foreach, context, base, config,
            root_execution, root_span_id,
        )

    async def _foreach_loop(
        self,
        exec_step: Step,
        fcfg: ForeachConfig,
        context: dict[str, Any],
        base: str,
        config: OrchestrationConfig,
        root_execution: Execution | None,
        root_span_id: int | None,
    ) -> StructuredResult:
        """Fan out over an array and execute *exec_step* once per element.

        Parameters
        ----------
        exec_step : Step
            The step whose request template is executed per iteration.
        fcfg : ForeachConfig
            The foreach configuration (array binding, loop var, mode).
        context : dict
            The accumulated execution context.
        base : str
            Base URL for relative step paths.
        config : OrchestrationConfig
            The full orchestration config (for source_id, etc.).
        root_execution, root_span_id
            Trace context for child-span linkage.

        Returns
        -------
        StructuredResult
            Merged result in ``merge`` mode; the last element's result in
            ``separate`` mode.

        Raises
        ------
        DataSourceError
            With ``FOREACH_SCHEMA_MISMATCH`` when merge columns diverge.
        """
        array = _resolve_path(context, fcfg.over)
        if not isinstance(array, list):
            raise DataSourceError(
                code="INVALID_BINDING",
                message=f"Foreach array_binding {fcfg.over!r} resolved to "
                f"{type(array).__name__}, expected a list.",
                suggestion="Ensure the upstream step produces an array.",
            )

        item_var = fcfg.as_
        merge_mode = fcfg.is_merge()

        all_rows: list[dict[str, Any]] = []
        all_columns: list[ColumnMeta] | None = None
        separate_results: list[StructuredResult] = []

        for idx, element in enumerate(array):
            # Create per-iteration context
            iter_context: dict[str, Any] = {**context}
            iter_context[item_var] = element

            # Interpolate
            url = interpolate(exec_step.path, iter_context)
            if not url.startswith(("http://", "https://")):
                url = base.rstrip("/") + "/" + url.lstrip("/")
            headers = {
                k: interpolate(v, iter_context)
                for k, v in exec_step.headers.items()
            }
            body = interpolate_dict(exec_step.body_template, iter_context)

            # Open foreach child span
            fspan: Span | None = None
            if self._trace is not None and root_execution is not None:
                fspan = Span(
                    id=0,
                    execution_id=root_execution.id,
                    parent_span_id=root_span_id,
                    span_kind=SpanKind.http_request,
                    span_name=f"{exec_step.name}[{idx}]",
                    source_id=config.id,
                    started_at=0,
                    attributes={
                        "url": url,
                        "method": exec_step.method,
                        "foreach_index": idx,
                        "foreach_item": item_var,
                    },
                )

            fstatus = SpanStatus.ok
            try:
                response = await self._http.request(
                    method=exec_step.method,
                    url=url,
                    headers=headers,
                    json=body,
                    follow_redirects=True,
                )

                if response.status_code >= 400:
                    fstatus = SpanStatus.error
                    raise DataSourceError(
                        code="STEP_FAILED",
                        message=f"Foreach iteration {idx} (step {exec_step.name!r}) failed "
                        f"with HTTP {response.status_code}: {response.text[:500]}",
                        suggestion=f"URL: {url}. Earlier iterations may have "
                        f"succeeded.",
                    )

                data = response.json()

                # Extract rows from response
                rows = self._extract_rows(data)
                columns = self._infer_columns(rows)

                if merge_mode:
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

        if merge_mode:
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

    # ── Output helpers ───────────────────────────────────────────────────

    @staticmethod
    def _store_output(step: Step, context: dict[str, Any], data: Any) -> None:
        """Store a step's parsed response into the execution context.

        - When ``step.output`` (alias → JSON-path map) is non-empty, each
          alias is extracted from *data* and bound under the step name.
        - Otherwise the legacy ``output_key`` (or step name) is used to bind
          the full response, preserving the original behaviour.
        """
        if step.output:
            extracted: dict[str, Any] = {}
            for alias, json_path in step.output.items():
                try:
                    extracted[alias] = _resolve_path(data, json_path)
                except DataSourceError:
                    extracted[alias] = None
            context[step.name] = extracted
        else:
            output_key = step.output_key or step.name
            context[output_key] = data

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
