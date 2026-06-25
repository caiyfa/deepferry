import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, SidecarError } from "@/api/client";
import type { Execution } from "@/api/types";
import { ExecutionDetail } from "@/components/ExecutionDetail";
import { ErrorBanner } from "@/components/ErrorBanner";
import { EmptyState } from "@/components/EmptyState";

export function ExecutionPage() {
  const { id } = useParams<{ id: string }>();
  const queryId = Number(id);

  const [execution, setExecution] = useState<Execution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarError | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!Number.isFinite(queryId)) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    setNotFound(false);
    api
      .listExecutions(200)
      .then((executions) => {
        if (!active) return;
        const match = executions.find((e) => e.root_query_id === queryId) ?? null;
        if (match) {
          setExecution(match);
        } else {
          setExecution(null);
          setNotFound(true);
        }
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof SidecarError ? err : null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [queryId]);

  return (
    <div className="df-content">
      <div className="df-pagehead">
        <div className="df-row">
          <Link to="/history" className="df-btn df-btn--ghost df-btn--sm">
            ← back to history
          </Link>
        </div>
      </div>

      {error ? <ErrorBanner error={error} /> : null}

      {loading ? (
        <div className="df-skeleton">
          <div className="df-skeleton__line" style={{ width: "30%" }} />
          <div className="df-skeleton__line" style={{ width: "90%" }} />
          <div className="df-skeleton__line" style={{ width: "70%" }} />
        </div>
      ) : null}

      {!loading && !error && execution ? (
        <ExecutionDetail execution={execution} />
      ) : null}

      {!loading && !error && notFound ? (
        <EmptyState
          glyph="∅"
          title={`No execution trace for query #${queryId}`}
          sub="This query may predate the audit-trace feature, or no spans were recorded for it."
        />
      ) : null}
    </div>
  );
}
