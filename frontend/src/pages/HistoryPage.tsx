import { useHistory } from "@/hooks/useHistory";
import { QueryHistory } from "@/components/QueryHistory";
import { ErrorBanner } from "@/components/ErrorBanner";

export function HistoryPage() {
  const { items, loading, error, refresh } = useHistory(100);

  return (
    <div className="df-content">
      <div className="df-pagehead">
        <div className="df-row">
          <h1 className="df-pagehead__title">history</h1>
          <div className="df-grow" />
          <button className="df-btn df-btn--ghost df-btn--sm" onClick={refresh} disabled={loading}>
            ↻ refresh
          </button>
        </div>
        <p className="df-pagehead__sub">Recent queries with row counts, status, and execution traces.</p>
      </div>

      {error ? <ErrorBanner error={error} /> : null}

      <QueryHistory items={items} loading={loading} />
    </div>
  );
}
