import { useQuery } from "@/context/QueryContext";
import { DataSourceSelector } from "@/components/DataSourceSelector";
import { QueryEditor } from "@/components/QueryEditor";
import { ResultGrid } from "@/components/ResultGrid";
import { ErrorBanner } from "@/components/ErrorBanner";
import { EmptyState } from "@/components/EmptyState";

export function QueryPage() {
  const { sourceId, statement, result, loading, error, setSourceId, setStatement, execute, clearResult } =
    useQuery();

  return (
    <div className="df-workspace">
      <div className="df-workspace__bar">
        <DataSourceSelector onSelect={setSourceId} selectedId={sourceId} />
      </div>

      <QueryEditor
        sourceId={sourceId}
        statement={statement}
        onStatementChange={setStatement}
        onExecute={execute}
        loading={loading}
      />

      {error ? (
        <div className="df-fade-in">
          <ErrorBanner error={error} onDismiss={clearResult} />
        </div>
      ) : null}

      {result && !error ? (
        <ResultGrid result={result} />
      ) : !loading && !error ? (
        <div className="df-workspace__result">
          <EmptyState
            glyph="≋"
            title="No results yet"
            sub="Pick a data source, write a query, and hit execute. Results render here in a virtualized grid."
          />
        </div>
      ) : null}
    </div>
  );
}
