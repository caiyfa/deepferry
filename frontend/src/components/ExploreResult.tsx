/**
 * ExploreResult — renders an orchestration result: summary, optional
 * cross-source badges, an AG Grid table, an auto-detected chart, an action
 * bar (copy SQL / CSV export / save), and a collapsible SQL panel.
 *
 * The `ExploreResultData` shape is defined locally (not imported from the
 * explore API) so this component stays decoupled from transport details.
 */

import { useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  type ColDef,
  type GridApi,
  type GridReadyEvent,
} from "ag-grid-community";
import type { ColumnMeta } from "@/api/types";
import { deepferryGridTheme } from "@/styles/ag-grid-theme";
import { AutoChart } from "@/components/AutoChart";

ModuleRegistry.registerModules([AllCommunityModule]);

interface ExploreResultData {
  summary: string;
  sql: string;
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
  row_count: number;
  execution_time_ms: number;
  source_ids: string[];
}

interface ExploreResultProps {
  result: ExploreResultData;
  sourceLabels?: string[];
  onSave?: (result: ExploreResultData) => void;
}

function exportCSV(columns: ColumnMeta[], rows: Record<string, unknown>[]): void {
  const headers = columns.map((c) => c.name).join(",");
  const lines = rows.map((row) =>
    columns.map((c) => JSON.stringify(row[c.name] ?? "")).join(","),
  );
  const csv = [headers, ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "explore_result.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export function ExploreResult({
  result,
  sourceLabels,
  onSave,
}: ExploreResultProps) {
  const gridRef = useRef<AgGridReact<Record<string, unknown>>>(null);
  const gridApiRef = useRef<GridApi | null>(null);
  const [sqlOpen, setSqlOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const columnDefs = useMemo<ColDef<Record<string, unknown>>[]>(
    () =>
      result.columns.map((col) => ({
        headerName: col.name,
        field: col.name,
        resizable: true,
        sortable: true,
      })),
    [result.columns],
  );

  const defaultColDef = useMemo<ColDef<Record<string, unknown>>>(
    () => ({ flex: 1, minWidth: 100 }),
    [],
  );

  const onGridReady = (event: GridReadyEvent<Record<string, unknown>>) => {
    gridApiRef.current = event.api;
  };

  const handleCopySQL = async () => {
    try {
      await navigator.clipboard.writeText(result.sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  const handleExportCSV = () => {
    exportCSV(result.columns, result.rows);
  };

  const handleSave = () => {
    onSave?.(result);
  };

  const isMultiSource = result.source_ids.length > 1;
  const labels = sourceLabels ?? result.source_ids;

  const summaryStyle: React.CSSProperties = {
    padding: "var(--df-space-3)",
    background: "var(--df-accent-soft)",
    color: "var(--df-fg)",
    fontSize: "var(--df-fs-sm)",
    borderRadius: "var(--df-radius)",
    lineHeight: "var(--df-lh-base)",
  };

  const badgeRowStyle: React.CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    gap: "var(--df-space-2)",
    marginTop: "var(--df-space-2)",
  };

  const badgeStyle: React.CSSProperties = {
    padding: "var(--df-space-1) var(--df-space-3)",
    borderRadius: "var(--df-radius-pill)",
    background: "var(--df-bg-elevated)",
    border: "1px solid var(--df-border-strong)",
    color: "var(--df-fg-muted)",
    fontFamily: "var(--df-font-mono)",
    fontSize: "var(--df-fs-xs)",
  };

  const crossBadgeStyle: React.CSSProperties = {
    ...badgeStyle,
    borderColor: "var(--df-accent-line)",
    color: "var(--df-accent)",
  };

  const gridHostStyle: React.CSSProperties = {
    marginTop: "var(--df-space-3)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius)",
    overflow: "hidden",
    height: 360,
  };

  const actionBarStyle: React.CSSProperties = {
    display: "flex",
    gap: "var(--df-space-2)",
    marginTop: "var(--df-space-3)",
    flexWrap: "wrap",
  };

  const sqlToggleStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "var(--df-space-2)",
    marginTop: "var(--df-space-3)",
    fontFamily: "var(--df-font-mono)",
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg-muted)",
    cursor: "pointer",
    userSelect: "none",
  };

  const sqlPreStyle: React.CSSProperties = {
    marginTop: "var(--df-space-2)",
    background: "var(--df-bg)",
    color: "var(--df-fg)",
    fontFamily: "var(--df-font-mono)",
    fontSize: "var(--df-fs-xs)",
    padding: "var(--df-space-3)",
    borderRadius: "var(--df-radius-sm)",
    overflowX: "auto",
    border: "1px solid var(--df-border)",
    lineHeight: "var(--df-lh-base)",
  };

  return (
    <div>
      <div style={summaryStyle}>{result.summary}</div>

      {isMultiSource ? (
        <div style={badgeRowStyle}>
          {labels.map((label, i) => (
            <span key={`${result.source_ids[i] ?? label}-${i}`} style={badgeStyle}>
              {label}
            </span>
          ))}
          <span style={crossBadgeStyle}>cross-source</span>
        </div>
      ) : null}

      <div style={gridHostStyle}>
        <AgGridReact<Record<string, unknown>>
          ref={gridRef}
          theme={deepferryGridTheme}
          rowData={result.rows}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          onGridReady={onGridReady}
        />
      </div>

      <AutoChart columns={result.columns} rows={result.rows} />

      <div style={actionBarStyle}>
        <button
          type="button"
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={handleCopySQL}
        >
          {copied ? "✓ copied" : "📋 copy sql"}
        </button>
        <button
          type="button"
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={handleExportCSV}
        >
          📥 csv export
        </button>
        <button
          type="button"
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={handleSave}
        >
          💾 save as dataset
        </button>
      </div>

      <div
        style={sqlToggleStyle}
        onClick={() => setSqlOpen((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setSqlOpen((v) => !v);
          }
        }}
      >
        <span>{sqlOpen ? "▾" : "▸"}</span>
        <span>sql</span>
        <span style={{ color: "var(--df-fg-subtle)" }}>
          · {result.execution_time_ms.toFixed(1)}ms · {result.row_count} rows
        </span>
      </div>

      {sqlOpen ? (
        <pre style={sqlPreStyle}>{result.sql}</pre>
      ) : null}
    </div>
  );
}