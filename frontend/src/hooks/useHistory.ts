import { useCallback, useEffect, useState } from "react";
import { api, SidecarError } from "@/api/client";
import type { QueryHistoryItem } from "@/api/types";

interface HistoryState {
  items: QueryHistoryItem[];
  loading: boolean;
  error: SidecarError | null;
  refresh: () => Promise<void>;
}

export function useHistory(limit = 50): HistoryState {
  const [items, setItems] = useState<QueryHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SidecarError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getHistory(limit);
      setItems(data);
    } catch (err) {
      setError(err instanceof SidecarError ? err : null);
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { items, loading, error, refresh };
}
