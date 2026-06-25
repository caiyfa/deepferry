import { useQuery } from "@/context/QueryContext";
import { ResultGrid } from "@/components/ResultGrid";
import { ErrorBanner } from "@/components/ErrorBanner";
import { EmptyState } from "@/components/EmptyState";

export function ResultsPage() {
  const { result, error, clearResult } = useQuery();

  return (
    <div className="df-content df-content--flush">
      <div style={{ padding: "var(--df-space-6) var(--df-space-8) 0" }}>
        <div className="df-pagehead">
          <h1 className="df-pagehead__title">results</h1>
          <p className="df-pagehead__sub">Most recent query result set.</p>
        </div>
        {error ? <ErrorBanner error={error} onDismiss={clearResult} /> : null}
      </div>

      {result && !error ? (
        <div style={{ flex: 1, minHeight: 0, padding: "var(--df-space-4) var(--df-space-8) var(--df-space-8)", display: "flex", flexDirection: "column" }}>
          <ResultGrid result={result} />
        </div>
      ) : !error ? (
        <EmptyState
          glyph="▦"
          title="Nothing to show"
          sub="Run a query from the Query workspace to populate this view."
        />
      ) : null}
    </div>
  );
}
