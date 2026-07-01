import { type CSSProperties } from "react";
import type { DiffResult } from "@/api/datasets";
import { EmptyState } from "@/components/EmptyState";

export interface DiffViewProps {
  diff: DiffResult | null;
  loading?: boolean;
}

type Accent = "green" | "red" | "yellow";

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--df-space-4)",
};

const summaryBarStyle: CSSProperties = {
  display: "flex",
  gap: "var(--df-space-4)",
  padding: "var(--df-space-3)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  marginBottom: "var(--df-space-3)",
  background: "var(--df-bg-elevated)",
  flexWrap: "wrap",
};

function summaryItemStyle(color: string): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: "var(--df-space-1)",
    fontSize: "var(--df-fs-sm)",
    color,
    fontFamily: "var(--df-font-mono)",
  };
}

const summaryIconStyle: CSSProperties = {
  fontSize: "var(--df-fs-md)",
  lineHeight: 1,
};

function sectionStyle(accent: Accent): CSSProperties {
  return {
    borderLeft: `3px solid var(--df-${accent})`,
    paddingLeft: "var(--df-space-3)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--df-space-2)",
  };
}

const sectionTitleStyle: CSSProperties = {
  fontSize: "var(--df-fs-sm)",
  fontWeight: 600,
  color: "var(--df-fg)",
  letterSpacing: "var(--df-tracking-tight)",
};

const sectionEmptyStyle: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  fontStyle: "italic",
};

const tableWrapStyle: CSSProperties = {
  overflowX: "auto",
  borderRadius: "var(--df-radius)",
  border: "1px solid var(--df-border)",
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "var(--df-fs-xs)",
  fontFamily: "var(--df-font-mono)",
};

const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "var(--df-space-2) var(--df-space-3)",
  borderBottom: "1px solid var(--df-border)",
  color: "var(--df-fg-subtle)",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  fontSize: "var(--df-fs-xs)",
  whiteSpace: "nowrap",
  background: "var(--df-bg-elevated)",
};

const tdStyle: CSSProperties = {
  padding: "var(--df-space-2) var(--df-space-3)",
  borderBottom: "1px solid var(--df-border)",
  color: "var(--df-fg)",
  whiteSpace: "nowrap",
};

const modRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--df-space-2)",
  padding: "var(--df-space-2)",
  borderBottom: "1px solid var(--df-border)",
  alignItems: "center",
  flexWrap: "wrap",
  fontFamily: "var(--df-font-mono)",
  fontSize: "var(--df-fs-xs)",
};

const modKeyStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
};

const beforeStyle: CSSProperties = {
  color: "var(--df-red)",
  textDecoration: "line-through",
};

const arrowStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
};

const afterStyle: CSSProperties = {
  color: "var(--df-green)",
};

function extractColumns(rows: Record<string, unknown>[]): string[] {
  const keys = new Set<string>();
  for (const row of rows) {
    for (const k of Object.keys(row)) keys.add(k);
  }
  return [...keys];
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

interface DiffTableProps {
  rows: Record<string, unknown>[];
  label: string;
}

function DiffTable({ rows, label }: DiffTableProps) {
  if (rows.length === 0) {
    return <div style={sectionEmptyStyle}>No {label}.</div>;
  }
  const columns = extractColumns(rows);
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} style={thStyle}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} style={tdStyle}>
                  {renderCell(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface SummaryItemProps {
  icon: string;
  count: number;
  label: string;
  color: string;
}

function SummaryItem({ icon, count, label, color }: SummaryItemProps) {
  return (
    <span style={summaryItemStyle(color)}>
      <span style={summaryIconStyle}>{icon}</span>
      <span>{count}</span>
      <span style={{ color: "var(--df-fg-muted)" }}>{label}</span>
    </span>
  );
}

export function DiffView({ diff, loading = false }: DiffViewProps) {
  if (loading) {
    return (
      <div className="df-skeleton">
        <div className="df-skeleton__line" style={{ width: "45%" }} />
        <div className="df-skeleton__line" style={{ width: "92%" }} />
        <div className="df-skeleton__line" style={{ width: "78%" }} />
        <div className="df-skeleton__line" style={{ width: "60%" }} />
      </div>
    );
  }

  if (!diff) {
    return (
      <EmptyState
        glyph="⇄"
        title="Select two versions to compare"
        sub="Pick a base and target version above, then run Compare."
      />
    );
  }

  const { additions, deletions, modifications, summary } = diff;

  return (
    <div className="df-fade-in" style={rootStyle}>
      <div style={summaryBarStyle}>
        <SummaryItem icon="+" count={summary.added} label="added" color="var(--df-green)" />
        <SummaryItem icon="−" count={summary.deleted} label="deleted" color="var(--df-red)" />
        <SummaryItem icon="~" count={summary.modified} label="modified" color="var(--df-yellow)" />
        <SummaryItem icon="=" count={summary.unchanged} label="unchanged" color="var(--df-fg-subtle)" />
      </div>

      <div style={sectionStyle("green")}>
        <div style={sectionTitleStyle}>Additions ({additions.length})</div>
        <DiffTable rows={additions} label="additions" />
      </div>

      <div style={sectionStyle("red")}>
        <div style={sectionTitleStyle}>Deletions ({deletions.length})</div>
        <DiffTable rows={deletions} label="deletions" />
      </div>

      <div style={sectionStyle("yellow")}>
        <div style={sectionTitleStyle}>Modifications ({modifications.length})</div>
        {modifications.length === 0 ? (
          <div style={sectionEmptyStyle}>No modifications.</div>
        ) : (
          modifications.map((mod, i) => (
            <div key={i} style={modRowStyle}>
              <span style={modKeyStyle}>Key: {JSON.stringify(mod.key)}</span>
              <span style={beforeStyle}>{JSON.stringify(mod.before)}</span>
              <span style={arrowStyle}>→</span>
              <span style={afterStyle}>{JSON.stringify(mod.after)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
