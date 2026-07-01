import { useEffect, useState } from "react";
import { useShellStore } from "@/store/shell";
import { api } from "@/api/client";
import type { SourceSummary } from "@/api/types";

type DotClass = "df-dot--ok" | "df-dot--err" | "df-dot--warn";

function healthToDot(health: SourceSummary["health"]): DotClass {
  switch (health) {
    case "healthy":
      return "df-dot--ok";
    case "unhealthy":
      return "df-dot--err";
    default:
      return "df-dot--warn";
  }
}

export function StatusBar() {
  const selectedSources = useShellStore((s) => s.selectedSources);
  const [sources, setSources] = useState<SourceSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .listSources()
      .then((all) => {
        if (cancelled) return;
        const selected = new Set(selectedSources);
        setSources(all.filter((s) => selected.has(s.id)));
      })
      .catch(() => {
        if (!cancelled) setSources([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSources]);

  return (
    <footer className="df-statusbar" role="contentinfo">
      {sources.length === 0 ? (
        <div className="df-statusbar__item">
          <span className="df-dot df-dot--idle" />
          <span className="df-subtle">no sources selected</span>
        </div>
      ) : (
        sources.map((source) => (
          <div className="df-statusbar__item" key={source.id}>
            <span className={`df-dot ${healthToDot(source.health)}`} />
            <span>{source.name}</span>
          </div>
        ))
      )}

      <div className="df-statusbar__spacer" />

      <div className="df-statusbar__item">
        <span className="df-subtle">agents:</span>
        <span>0 agents</span>
      </div>

      <div className="df-statusbar__item">
        <span className="df-subtle">last activity:</span>
        <span>—</span>
      </div>

      <div className="df-statusbar__item">
        <span className="df-subtle">v0.1.0 · m3</span>
      </div>
    </footer>
  );
}