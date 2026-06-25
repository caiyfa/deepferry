import type { Execution } from "@/api/types";
import { SpanTree } from "./SpanTree";

interface ExecutionDetailProps {
  execution: Execution;
}

function formatUnix(ms: number): string {
  return new Date(ms).toLocaleString();
}

function executionDurationMs(exec: Execution): number | null {
  if (exec.finished_at == null) return null;
  return exec.finished_at - exec.started_at;
}

export function ExecutionDetail({ execution }: ExecutionDetailProps) {
  const dur = executionDurationMs(execution);
  const spanCount = execution.spans.length;
  const errorSpans = execution.spans.filter((s) => s.status === "error").length;
  const retrySpans = execution.spans.filter((s) => s.span_kind === "auth_retry").length;

  return (
    <div className="df-exec df-fade-in">
      <div className="df-exec__header">
        <h2 className="df-pagehead__title">
          execution <span className="df-subtle">#{execution.id}</span>
        </h2>
        <span
          className={`df-badge ${execution.status === "ok" ? "df-badge--ok" : "df-badge--err"}`}
        >
          {execution.status}
        </span>
        <span className="df-badge df-badge--info">{execution.source_id}</span>
        {execution.scenario_id ? (
          <span className="df-badge">scenario: {execution.scenario_id}</span>
        ) : null}
      </div>

      <div className="df-row df-row--wrap">
        <div className="df-card" style={{ minWidth: 200 }}>
          <div className="df-card__head">
            <span className="df-card__title">started</span>
          </div>
          <div className="df-card__body df-mono" style={{ fontSize: "var(--df-fs-sm)" }}>
            {formatUnix(execution.started_at)}
          </div>
        </div>
        <div className="df-card" style={{ minWidth: 200 }}>
          <div className="df-card__head">
            <span className="df-card__title">duration</span>
          </div>
          <div className="df-card__body df-mono" style={{ fontSize: "var(--df-fs-md)" }}>
            {dur == null ? "—" : `${dur}ms`}
          </div>
        </div>
        <div className="df-card" style={{ minWidth: 160 }}>
          <div className="df-card__head">
            <span className="df-card__title">spans</span>
          </div>
          <div className="df-card__body df-mono" style={{ fontSize: "var(--df-fs-md)" }}>
            {spanCount}
          </div>
        </div>
        {errorSpans > 0 ? (
          <div className="df-card" style={{ minWidth: 160 }}>
            <div className="df-card__head">
              <span className="df-card__title">errors</span>
            </div>
            <div className="df-card__body df-mono" style={{ fontSize: "var(--df-fs-md)", color: "var(--df-danger)" }}>
              {errorSpans}
            </div>
          </div>
        ) : null}
        {retrySpans > 0 ? (
          <div className="df-card" style={{ minWidth: 160 }}>
            <div className="df-card__head">
              <span className="df-card__title">retries</span>
            </div>
            <div className="df-card__body df-mono" style={{ fontSize: "var(--df-fs-md)", color: "var(--df-auth-retry)" }}>
              {retrySpans}
            </div>
          </div>
        ) : null}
      </div>

      <div>
        <div className="df-pagehead__sub" style={{ marginBottom: "var(--df-space-3)" }}>
          span timeline — click a row to expand attributes
        </div>
        <SpanTree spans={execution.spans} />
      </div>
    </div>
  );
}
