import { themeAlpine } from "ag-grid-community";

export const deepferryGridTheme = themeAlpine.withParams({
  backgroundColor: "transparent",
  foregroundColor: "#cdd6f4",
  borderColor: "rgba(205, 214, 244, 0.1)",
  borderRadius: "6px",
  headerTextColor: "#a6adc8",
  headerCellHoverBackgroundColor: "#313244",
  oddRowBackgroundColor: "rgba(49, 50, 68, 0.35)",
  rowHoverColor: "rgba(69, 71, 90, 0.5)",
  selectedRowBackgroundColor: "rgba(137, 180, 250, 0.16)",
  rangeSelectionBorderColor: "#89b4fa",
  rangeSelectionBackgroundColor: "rgba(137, 180, 250, 0.12)",
  headerColumnResizeHandleColor: "#89b4fa",
  columnHoverColor: "rgba(137, 180, 250, 0.06)",
  cellTextColor: "#cdd6f4",
  cellFontSize: "13px",
  cellHorizontalPadding: 12,
  fontFamily:
    '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, monospace',
  rowBorder: { style: "solid", width: 1, color: "rgba(205, 214, 244, 0.06)" },
  headerRowBorder: { style: "solid", width: 1, color: "rgba(205, 214, 244, 0.12)" },
});
