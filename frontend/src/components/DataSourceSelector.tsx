import { useEffect, useMemo } from "react";
import { useSources } from "@/hooks/useSources";
import { ErrorBanner } from "./ErrorBanner";
import type { SourceHealth } from "@/api/types";

interface DataSourceSelectorProps {
  onSelect: (sourceId: string) => void;
  selectedId: string;
}

const HEALTH_BADGE: Record<SourceHealth, { cls: string; label: string }> = {
  healthy: { cls: "df-badge--ok", label: "healthy" },
  unhealthy: { cls: "df-badge--err", label: "unhealthy" },
  unknown: { cls: "df-badge--warn", label: "unknown" },
};

export function DataSourceSelector({ onSelect, selectedId }: DataSourceSelectorProps) {
  const { sources, loading, error, refresh } = useSources();

  useEffect(() => {
    if (!selectedId && sources.length > 0) {
      onSelect(sources[0].id);
    }
  }, [sources, selectedId, onSelect]);

  const selected = useMemo(
    () => sources.find((s) => s.id === selectedId) ?? null,
    [sources, selectedId],
  );

  return (
    <div className="df-field df-workspace__source">
      <div className="df-row df-row--wrap">
        <span className="df-field__label">data source</span>
        <button
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={refresh}
          disabled={loading}
          aria-label="Refresh data sources"
        >
          {loading ? "⟳" : "↻"} refresh
        </button>
      </div>

      <select
        className="df-select"
        value={selectedId}
        onChange={(e) => onSelect(e.target.value)}
        disabled={loading && sources.length === 0}
        aria-label="Select data source"
      >
        {sources.length === 0 && !loading ? (
          <option value="">No sources configured</option>
        ) : null}
        {loading && sources.length === 0 ? (
          <option value="">Loading sources…</option>
        ) : null}
        {sources.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} · {s.type} · {s.health}
          </option>
        ))}
      </select>

      {selected ? (
        <div className="df-row">
          <span className={`df-badge ${HEALTH_BADGE[selected.health].cls}`}>
            {HEALTH_BADGE[selected.health].label}
          </span>
          <span className="df-badge df-badge--info">{selected.type}</span>
          <span className="df-mono df-subtle" style={{ fontSize: "var(--df-fs-xs)" }}>
            id: {selected.id}
          </span>
        </div>
      ) : null}

      {error ? <ErrorBanner error={error} /> : null}
    </div>
  );
}
