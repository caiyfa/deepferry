import { useMemo, useRef } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  type AutoSizeStrategy,
  type ColDef,
  type CsvExportParams,
  type GridApi,
  type GridReadyEvent,
  type RowSelectionOptions,
} from "ag-grid-community";
import type { QueryResult } from "@/api/types";
import { deepferryGridTheme } from "@/styles/ag-grid-theme";

ModuleRegistry.registerModules([AllCommunityModule]);

interface ResultGridProps {
  result: QueryResult;
  onExportCSV?: () => void;
}

const AUTO_SIZE_ROW_THRESHOLD = 2000;

function inferFilterType(colType: string): string {
  const t = colType.toLowerCase();
  if (/int|long|bigint|short|byte|numeric|decimal|float|double|real|number/.test(t)) {
    return "agNumberColumnFilter";
  }
  if (/date|time|timestamp/.test(t)) {
    return "agDateColumnFilter";
  }
  return "agTextColumnFilter";
}

export function ResultGrid({ result, onExportCSV }: ResultGridProps) {
  const gridRef = useRef<AgGridReact<Record<string, unknown>>>(null);
  const gridApiRef = useRef<GridApi | null>(null);

  const columnDefs = useMemo<ColDef<Record<string, unknown>>[]>(
    () =>
      result.columns.map((col) => ({
        field: col.name,
        headerName: col.name,
        headerTooltip: `${col.type}${col.nullable ? " · nullable" : ""}`,
        filter: inferFilterType(col.type),
        minWidth: 120,
        flex: 1,
        resizable: true,
        sortable: true,
        editable: true,
        cellRenderer: undefined,
      })),
    [result.columns],
  );

  const defaultColDef = useMemo<ColDef<Record<string, unknown>>>(
    () => ({
      flex: 1,
      minWidth: 120,
      resizable: true,
      sortable: true,
      editable: true,
      floatingFilter: true,
    }),
    [],
  );

  const autoSizeStrategy = useMemo<AutoSizeStrategy>(() => {
    if (result.row_count <= AUTO_SIZE_ROW_THRESHOLD) {
      return { type: "fitCellContents" };
    }
    return { type: "fitGridWidth" };
  }, [result.row_count]);

  const rowSelection = useMemo<RowSelectionOptions<Record<string, unknown>>>(
    () => ({ mode: "multiRow", checkboxes: false, enableClickSelection: true }),
    [],
  );

  const onGridReady = (event: GridReadyEvent<Record<string, unknown>>) => {
    gridApiRef.current = event.api;
  };

  const handleExport = () => {
    const api = gridApiRef.current ?? gridRef.current?.api;
    if (!api) return;
    const params: CsvExportParams = {
      fileName: `deepferry-results-${Date.now()}.csv`,
      suppressQuotes: false,
    };
    api.exportDataAsCsv(params);
    onExportCSV?.();
  };

  return (
    <div className="df-workspace__result">
      <div className="df-result-toolbar">
        <div className="df-result-toolbar__stat">
          <span className="df-subtle">rows</span>
          <strong>{result.row_count.toLocaleString()}</strong>
        </div>
        <div className="df-result-toolbar__stat">
          <span className="df-subtle">cols</span>
          <strong>{result.columns.length}</strong>
        </div>
        <div className="df-result-toolbar__stat">
          <span className="df-subtle">time</span>
          <strong>{result.execution_time_ms.toFixed(1)}ms</strong>
        </div>
        <div className="df-result-toolbar__stat">
          <span className="df-subtle">src</span>
          <strong>{result.source_id}</strong>
        </div>
        <div className="df-result-toolbar__spacer" />
        <button className="df-btn df-btn--ghost df-btn--sm" onClick={handleExport}>
          ⤓ export csv
        </button>
      </div>
      <div className="df-grid-host">
        <AgGridReact<Record<string, unknown>>
          ref={gridRef}
          theme={deepferryGridTheme}
          rowData={result.rows}
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          autoSizeStrategy={autoSizeStrategy}
          rowSelection={rowSelection}
          suppressCellFocus={false}
          animateRows={false}
          onGridReady={onGridReady}
        />
      </div>
    </div>
  );
}
