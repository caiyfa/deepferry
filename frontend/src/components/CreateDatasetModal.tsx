import { useState, type CSSProperties } from "react";
import {
  datasetsApi,
  type DatasetFormat,
  type RefreshStrategy,
  type Dataset,
} from "@/api/datasets";

export interface CreateDatasetModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultSql?: string;
  defaultSourceIds?: string[];
  onCreated?: (dataset: Dataset) => void;
}

const overlayStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  zIndex: 50,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const modalStyle: CSSProperties = {
  background: "var(--df-bg-elevated)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius-md)",
  padding: "var(--df-space-5)",
  width: "560px",
  maxWidth: "90vw",
  maxHeight: "80vh",
  overflowY: "auto",
};

const headerStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: "var(--df-space-4)",
  fontSize: "var(--df-fs-md)",
  fontWeight: 600,
  color: "var(--df-fg)",
};

const labelStyle: CSSProperties = {
  display: "block",
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: "var(--df-space-1)",
};

const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--df-bg)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-2)",
  color: "var(--df-fg)",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
  boxSizing: "border-box",
};

const textareaStyle: CSSProperties = {
  ...inputStyle,
  resize: "vertical",
  minHeight: "4rem",
};

const sqlTextareaStyle: CSSProperties = {
  ...inputStyle,
  resize: "vertical",
  minHeight: "80px",
  fontFamily: "var(--df-font-mono)",
  fontSize: "var(--df-fs-xs)",
};

const badgeRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--df-space-1)",
  flexWrap: "wrap",
};

const badgeStyle: CSSProperties = {
  background: "var(--df-accent-soft)",
  color: "var(--df-accent)",
  padding: "2px var(--df-space-2)",
  borderRadius: "var(--df-radius-pill)",
  fontSize: "var(--df-fs-xs)",
};

const buttonRowStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "var(--df-space-2)",
  marginTop: "var(--df-space-5)",
};

const cancelButtonStyle: CSSProperties = {
  background: "transparent",
  color: "var(--df-fg)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-2) var(--df-space-4)",
  cursor: "pointer",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
};

const createButtonStyle: CSSProperties = {
  background: "var(--df-accent)",
  color: "white",
  border: "none",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-2) var(--df-space-4)",
  cursor: "pointer",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
};

const closeButtonStyle: CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--df-fg-subtle)",
  cursor: "pointer",
  fontSize: "var(--df-fs-md)",
  padding: 0,
  lineHeight: 1,
};

const errorStyle: CSSProperties = {
  color: "var(--df-red)",
  fontSize: "var(--df-fs-xs)",
  marginTop: "var(--df-space-2)",
};

const sectionGapStyle: CSSProperties = {
  marginBottom: "var(--df-space-3)",
};

const selectStyle: CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

const FORMAT_OPTIONS: DatasetFormat[] = ["parquet", "json", "arrow"];
const REFRESH_OPTIONS: RefreshStrategy[] = ["manual", "hourly", "daily", "weekly"];

export function CreateDatasetModal({
  isOpen,
  onClose,
  defaultSql,
  defaultSourceIds,
  onCreated,
}: CreateDatasetModalProps): JSX.Element | null {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sql, setSql] = useState(defaultSql ?? "");
  const [format, setFormat] = useState<DatasetFormat>("parquet");
  const [refreshStrategy, setRefreshStrategy] = useState<RefreshStrategy>("manual");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleCreate = async (): Promise<void> => {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (!sql.trim()) {
      setError("SQL query is required");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const dataset = await datasetsApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        source_ids: defaultSourceIds ?? [],
        sql: sql.trim(),
        format,
        refresh_strategy: refreshStrategy,
      });
      onCreated?.(dataset);
      onClose();
      setName("");
      setDescription("");
      setSql("");
      setFormat("parquet");
      setRefreshStrategy("manual");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create dataset");
    } finally {
      setCreating(false);
    }
  };

  const handleClose = (): void => {
    if (creating) return;
    setError(null);
    onClose();
  };

  return (
    <div style={overlayStyle} onClick={handleClose} role="presentation">
      <div
        style={modalStyle}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-dataset-title"
      >
        <div style={headerStyle}>
          <span id="create-dataset-title">Create Dataset</span>
          <button
            type="button"
            style={closeButtonStyle}
            onClick={handleClose}
            aria-label="Close"
            disabled={creating}
          >
            ✕
          </button>
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="cdm-name">
            Name
          </label>
          <input
            id="cdm-name"
            type="text"
            style={inputStyle}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Dataset"
            autoFocus
            disabled={creating}
          />
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="cdm-description">
            Description
          </label>
          <textarea
            id="cdm-description"
            style={textareaStyle}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
            disabled={creating}
          />
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="cdm-sql">
            SQL
          </label>
          <textarea
            id="cdm-sql"
            style={sqlTextareaStyle}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            placeholder="SELECT ..."
            disabled={creating}
          />
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="cdm-format">
            Format
          </label>
          <select
            id="cdm-format"
            style={selectStyle}
            value={format}
            onChange={(e) => setFormat(e.target.value as DatasetFormat)}
            disabled={creating}
          >
            {FORMAT_OPTIONS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="cdm-refresh">
            Refresh Strategy
          </label>
          <select
            id="cdm-refresh"
            style={selectStyle}
            value={refreshStrategy}
            onChange={(e) => setRefreshStrategy(e.target.value as RefreshStrategy)}
            disabled={creating}
          >
            {REFRESH_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>

        {defaultSourceIds && defaultSourceIds.length > 0 && (
          <div style={sectionGapStyle}>
            <label style={labelStyle}>Sources</label>
            <div style={badgeRowStyle}>
              {defaultSourceIds.map((sid) => (
                <span key={sid} style={badgeStyle}>
                  {sid}
                </span>
              ))}
            </div>
          </div>
        )}

        {error && <div style={errorStyle}>{error}</div>}

        <div style={buttonRowStyle}>
          <button
            type="button"
            style={cancelButtonStyle}
            onClick={handleClose}
            disabled={creating}
          >
            Cancel
          </button>
          <button
            type="button"
            style={createButtonStyle}
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
