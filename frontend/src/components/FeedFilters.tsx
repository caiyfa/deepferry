import type { CSSProperties } from "react";
import type { FilterState, FeedEventStatus } from "@/api/agents";

export interface FeedFiltersProps {
  filters: FilterState;
  onChange: (filters: FilterState) => void;
  agents: string[];
  sources: string[];
}

const containerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--df-space-3)",
  padding: "var(--df-space-2) var(--df-space-4)",
  borderBottom: "1px solid var(--df-border)",
  flexWrap: "wrap",
};

const selectStyle: CSSProperties = {
  background: "var(--df-bg)",
  color: "var(--df-fg)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-1) var(--df-space-2)",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
};

const groupStyle: CSSProperties = {
  display: "flex",
  gap: "2px",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  overflow: "hidden",
};

const labelStyle: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

type StatusOption = {
  value: FilterState["status"];
  label: string;
  dotColor: string;
  spin?: boolean;
};

const STATUS_OPTIONS: StatusOption[] = [
  { value: "all", label: "All", dotColor: "var(--df-fg-subtle)" },
  { value: "running", label: "Running", dotColor: "var(--df-yellow)", spin: true },
  { value: "ok", label: "OK", dotColor: "var(--df-green)" },
  { value: "error", label: "Error", dotColor: "var(--df-red)" },
];

function buttonStyle(active: boolean): CSSProperties {
  return {
    padding: "var(--df-space-1) var(--df-space-2)",
    border: "none",
    background: active ? "var(--df-accent-soft)" : "transparent",
    color: "var(--df-fg)",
    cursor: "pointer",
    fontSize: "var(--df-fs-xs)",
    fontFamily: "var(--df-font-sans)",
    display: "inline-flex",
    alignItems: "center",
    gap: "var(--df-space-1)",
  };
}

function dotStyle(color: string, spin: boolean | undefined): CSSProperties {
  return {
    display: "inline-block",
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    background: color,
    animation: spin ? "df-spin 1s linear infinite" : undefined,
  };
}

export function FeedFilters({ filters, onChange, agents, sources }: FeedFiltersProps) {
  const update = (patch: Partial<FilterState>) => {
    onChange({ ...filters, ...patch });
  };

  return (
    <div style={containerStyle} role="toolbar" aria-label="Activity feed filters">
      <span style={labelStyle}>Agent</span>
      <select
        style={selectStyle}
        value={filters.agent}
        aria-label="Filter by agent"
        onChange={(e) => update({ agent: e.target.value })}
      >
        <option value="all">All Agents</option>
        {agents.map((a) => (
          <option key={a} value={a}>
            {a}
          </option>
        ))}
      </select>

      <span style={labelStyle}>Status</span>
      <div style={groupStyle} role="group" aria-label="Filter by status">
        {STATUS_OPTIONS.map((opt) => {
          const active = filters.status === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              style={buttonStyle(active)}
              aria-pressed={active}
              onClick={() => update({ status: opt.value as FeedEventStatus | "all" })}
            >
              <span style={dotStyle(opt.dotColor, opt.spin)} />
              {opt.label}
            </button>
          );
        })}
      </div>

      <span style={labelStyle}>Source</span>
      <select
        style={selectStyle}
        value={filters.source}
        aria-label="Filter by source"
        onChange={(e) => update({ source: e.target.value })}
      >
        <option value="all">All Sources</option>
        {sources.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <style>{`
        @keyframes df-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}