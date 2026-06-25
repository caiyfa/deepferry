import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, SidecarError } from "@/api/client";
import type { QueryResult } from "@/api/types";

interface QueryContextValue {
  sourceId: string;
  statement: string;
  result: QueryResult | null;
  loading: boolean;
  error: SidecarError | null;
  setSourceId: (id: string) => void;
  setStatement: (sql: string) => void;
  execute: () => Promise<void>;
  clearResult: () => void;
}

const QueryContext = createContext<QueryContextValue | null>(null);

export function QueryProvider({ children }: { children: ReactNode }) {
  const [sourceId, setSourceId] = useState("");
  const [statement, setStatement] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<SidecarError | null>(null);

  const execute = useCallback(async () => {
    if (!sourceId || !statement.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.executeQuery({
        source_id: sourceId,
        statement,
      });
      setResult(data);
    } catch (err) {
      setResult(null);
      setError(err instanceof SidecarError ? err : null);
    } finally {
      setLoading(false);
    }
  }, [sourceId, statement]);

  const clearResult = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  const value = useMemo<QueryContextValue>(
    () => ({
      sourceId,
      statement,
      result,
      loading,
      error,
      setSourceId,
      setStatement,
      execute,
      clearResult,
    }),
    [sourceId, statement, result, loading, error, execute, clearResult],
  );

  return <QueryContext.Provider value={value}>{children}</QueryContext.Provider>;
}

export function useQuery(): QueryContextValue {
  const ctx = useContext(QueryContext);
  if (!ctx) {
    throw new Error("useQuery must be used within a QueryProvider");
  }
  return ctx;
}
