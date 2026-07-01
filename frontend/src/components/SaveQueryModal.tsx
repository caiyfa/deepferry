import { useState, type CSSProperties } from "react";
import { savedQueriesApi, extractParameters } from "@/api/saved-queries";

export interface SaveQueryModalProps {
  isOpen: boolean;
  onClose: () => void;
  statement: string;
  sourceIds: string[];
  onSaved?: () => void;
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

const sqlPreviewStyle: CSSProperties = {
  background: "var(--df-bg)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-3)",
  fontFamily: "var(--df-font-mono)",
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  whiteSpace: "pre-wrap",
  maxHeight: "12rem",
  overflowY: "auto",
};

const badgeRowStyle: CSSProperties = {
  display: "inline-flex",
  gap: "var(--df-space-1)",
  flexWrap: "wrap",
};

const badgeStyle: CSSProperties = {
  background: "var(--df-accent-soft)",
  color: "var(--df-accent)",
  padding: "2px var(--df-space-2)",
  borderRadius: "var(--df-radius-pill)",
  fontFamily: "var(--df-font-mono)",
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

const saveButtonStyle: CSSProperties = {
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

const mutedNoteStyle: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
};

export function SaveQueryModal({
  isOpen,
  onClose,
  statement,
  sourceIds,
  onSaved,
}: SaveQueryModalProps): JSX.Element | null {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const parameters = extractParameters(statement);

  const handleSave = async (): Promise<void> => {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await savedQueriesApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        statement,
        source_ids: sourceIds,
      });
      onSaved?.();
      onClose();
      setName("");
      setDescription("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save query");
    } finally {
      setSaving(false);
    }
  };

  const handleClose = (): void => {
    if (saving) return;
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
        aria-labelledby="save-query-title"
      >
        <div style={headerStyle}>
          <span id="save-query-title">Save Query</span>
          <button
            type="button"
            style={closeButtonStyle}
            onClick={handleClose}
            aria-label="Close"
            disabled={saving}
          >
            ✕
          </button>
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="sqm-name">
            Name
          </label>
          <input
            id="sqm-name"
            type="text"
            style={inputStyle}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My saved query"
            autoFocus
            disabled={saving}
          />
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle} htmlFor="sqm-description">
            Description
          </label>
          <textarea
            id="sqm-description"
            style={textareaStyle}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
            disabled={saving}
          />
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle}>Parameters detected</label>
          {parameters.length > 0 ? (
            <div style={badgeRowStyle}>
              {parameters.map((p) => (
                <span key={p} style={badgeStyle}>
                  {p}
                </span>
              ))}
            </div>
          ) : (
            <span style={mutedNoteStyle}>No parameters</span>
          )}
        </div>

        <div style={sectionGapStyle}>
          <label style={labelStyle}>SQL Preview</label>
          <div style={sqlPreviewStyle}>{statement}</div>
        </div>

        {error && <div style={errorStyle}>{error}</div>}

        <div style={buttonRowStyle}>
          <button
            type="button"
            style={cancelButtonStyle}
            onClick={handleClose}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            style={saveButtonStyle}
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save Query"}
          </button>
        </div>
      </div>
    </div>
  );
}