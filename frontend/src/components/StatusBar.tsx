import { useSidecarHealth } from "@/hooks/useSidecarHealth";
import { useQuery } from "@/context/QueryContext";

export function StatusBar() {
  const health = useSidecarHealth();
  const { sourceId, result, loading } = useQuery();

  const dotClass =
    health.state === "ok"
      ? "df-dot--ok"
      : health.state === "down"
        ? "df-dot--err"
        : "df-dot--idle";

  const label =
    health.state === "ok"
      ? "sidecar online"
      : health.state === "down"
        ? "sidecar offline"
        : "checking…";

  return (
    <footer className="df-statusbar" role="contentinfo">
      <div className="df-statusbar__item">
        <span className={`df-dot ${dotClass}`} />
        <span>{label}</span>
      </div>

      <div className="df-statusbar__item">
        <span className="df-subtle">src:</span>
        <span>{sourceId || "—"}</span>
      </div>

      <div className="df-statusbar__spacer" />

      {loading ? (
        <div className="df-statusbar__item">
          <span className="df-dot df-dot--warn" />
          <span>executing…</span>
        </div>
      ) : null}

      {result ? (
        <div className="df-statusbar__item">
          <span className="df-subtle">rows:</span>
          <span>{result.row_count.toLocaleString()}</span>
          <span className="df-subtle">·</span>
          <span className="df-subtle">ms:</span>
          <span>{result.execution_time_ms.toFixed(1)}</span>
        </div>
      ) : null}
    </footer>
  );
}
