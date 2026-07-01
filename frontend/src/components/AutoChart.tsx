/**
 * AutoChart — picks a sensible Recharts visualization based on column types.
 *
 * Heuristics:
 *   1 numeric + 1 date      → LineChart (time series)
 *   1 numeric + 1 category  → BarChart
 *   2 numeric               → ScatterChart
 *   no numeric              → null (chart skipped)
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ColumnMeta } from "@/api/types";

interface AutoChartProps {
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
}

function isNumericColumn(col: ColumnMeta): boolean {
  const t = col.type.toUpperCase();
  return [
    "INTEGER",
    "INT",
    "BIGINT",
    "SMALLINT",
    "REAL",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "NUMERIC",
  ].includes(t);
}

function isDateColumn(col: ColumnMeta): boolean {
  const t = col.type.toUpperCase();
  return ["DATE", "DATETIME", "TIMESTAMP", "TIME"].includes(t);
}

function isCategoryColumn(col: ColumnMeta): boolean {
  return !isNumericColumn(col) && !isDateColumn(col);
}

function toNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

type ChartKind = "bar" | "line" | "scatter" | null;

function pickChart(columns: ColumnMeta[]): {
  kind: ChartKind;
  xCol: ColumnMeta | null;
  yCol: ColumnMeta | null;
} {
  const numeric = columns.filter(isNumericColumn);
  const dates = columns.filter(isDateColumn);
  const categories = columns.filter(isCategoryColumn);

  if (numeric.length === 0) {
    return { kind: null, xCol: null, yCol: null };
  }

  // 1 numeric + 1 date → line (time series)
  if (numeric.length >= 1 && dates.length >= 1) {
    return { kind: "line", xCol: dates[0], yCol: numeric[0] };
  }

  // 1 numeric + 1 category → bar
  if (numeric.length >= 1 && categories.length >= 1) {
    return { kind: "bar", xCol: categories[0], yCol: numeric[0] };
  }

  // 2 numeric → scatter
  if (numeric.length >= 2) {
    return { kind: "scatter", xCol: numeric[0], yCol: numeric[1] };
  }

  return { kind: null, xCol: null, yCol: null };
}

export function AutoChart({ columns, rows }: AutoChartProps) {
  const { kind, xCol, yCol } = pickChart(columns);

  if (kind === null || !xCol || !yCol || rows.length === 0) {
    return null;
  }

  const data = rows.map((row) => ({
    x: row[xCol.name],
    y: toNumber(row[yCol.name]),
  }));

  const axisColor = "var(--df-fg-subtle)";
  const gridColor = "var(--df-border)";
  const accentColor = "var(--df-accent)";

  const containerStyle: React.CSSProperties = {
    marginTop: "var(--df-space-3)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius)",
    padding: "var(--df-space-3)",
    background: "var(--df-bg-elevated)",
  };

  return (
    <div style={containerStyle}>
      <ResponsiveContainer width="100%" height={220}>
        {kind === "bar" ? (
          <BarChart data={data}>
            <CartesianGrid stroke={gridColor} strokeDasharray="3 3" />
            <XAxis
              dataKey="x"
              stroke={axisColor}
              tick={{ fontSize: 11 }}
              tickLine={false}
            />
            <YAxis stroke={axisColor} tick={{ fontSize: 11 }} tickLine={false} />
            <Tooltip
              contentStyle={{
                background: "var(--df-bg-sunken)",
                border: "1px solid var(--df-border-strong)",
                borderRadius: "var(--df-radius-sm)",
                color: "var(--df-fg)",
                fontSize: "var(--df-fs-xs)",
              }}
            />
            <Bar dataKey="y" fill={accentColor} radius={[4, 4, 0, 0]} />
          </BarChart>
        ) : kind === "line" ? (
          <LineChart data={data}>
            <CartesianGrid stroke={gridColor} strokeDasharray="3 3" />
            <XAxis
              dataKey="x"
              stroke={axisColor}
              tick={{ fontSize: 11 }}
              tickLine={false}
            />
            <YAxis stroke={axisColor} tick={{ fontSize: 11 }} tickLine={false} />
            <Tooltip
              contentStyle={{
                background: "var(--df-bg-sunken)",
                border: "1px solid var(--df-border-strong)",
                borderRadius: "var(--df-radius-sm)",
                color: "var(--df-fg)",
                fontSize: "var(--df-fs-xs)",
              }}
            />
            <Line
              type="monotone"
              dataKey="y"
              stroke={accentColor}
              strokeWidth={2}
              dot={{ r: 3, fill: accentColor }}
            />
          </LineChart>
        ) : (
          <ScatterChart>
            <CartesianGrid stroke={gridColor} strokeDasharray="3 3" />
            <XAxis
              dataKey="x"
              stroke={axisColor}
              tick={{ fontSize: 11 }}
              tickLine={false}
              type="number"
            />
            <YAxis
              dataKey="y"
              stroke={axisColor}
              tick={{ fontSize: 11 }}
              tickLine={false}
              type="number"
            />
            <Tooltip
              contentStyle={{
                background: "var(--df-bg-sunken)",
                border: "1px solid var(--df-border-strong)",
                borderRadius: "var(--df-radius-sm)",
                color: "var(--df-fg)",
                fontSize: "var(--df-fs-xs)",
              }}
            />
            <Scatter data={data} fill={accentColor} />
          </ScatterChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}