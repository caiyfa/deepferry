import { useCallback, useEffect, useState } from "react";
import { api, SidecarError } from "@/api/client";
import type { SourceSummary } from "@/api/types";

interface SourcesState {
  sources: SourceSummary[];
  loading: boolean;
  error: SidecarError | null;
  refresh: () => Promise<void>;
}

export function useSources(autoRefreshMs = 0): SourcesState {
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listSources();
      setSources(data);
    } catch (err) {
      setError(err instanceof SidecarError ? err : null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!autoRefreshMs) return;
    const id = setInterval(refresh, autoRefreshMs);
    return () => clearInterval(id);
  }, [refresh, autoRefreshMs]);

  return { sources, loading, error, refresh };
}
