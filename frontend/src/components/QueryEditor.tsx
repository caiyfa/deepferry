import { useState } from "react";
import { api, SidecarError } from "@/api/client";
import type { ResourceMeta } from "@/api/types";
import { ErrorBanner } from "./ErrorBanner";

interface QueryEditorProps {
  sourceId: string;
  statement: string;
  onStatementChange: (sql: string) => void;
  onExecute: () => void;
  loading: boolean;
}

export function QueryEditor({
  sourceId,
  statement,
  onStatementChange,
  onExecute,
  loading,
}: QueryEditorProps) {
  const [schema, setSchema] = useState<ResourceMeta[] | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<SidecarError | null>(null);
  const [showSchema, setShowSchema] = useState(false);

  const loadSchema = async () => {
    if (!sourceId) return;
    setSchemaLoading(true);
    setSchemaError(null);
    try {
      const data = await api.getSchema(sourceId);
      setSchema(data.resources);
      setShowSchema(true);
    } catch (err) {
      setSchema(null);
      setSchemaError(err instanceof SidecarError ? err : null);
      setShowSchema(true);
    } finally {
      setSchemaLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      onExecute();
    }
    if (e.key === "Tab") {
      e.preventDefault();
      const target = e.currentTarget;
      const start = target.selectionStart;
      const end = target.selectionEnd;
      const next = statement.slice(0, start) + "  " + statement.slice(end);
      onStatementChange(next);
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = start + 2;
      });
    }
  };

  const canExecute = Boolean(sourceId) && statement.trim().length > 0 && !loading;

  return (
    <div className="df-workspace__editor">
      <div className="df-row df-row--wrap">
        <span className="df-field__label">sql query</span>
        <div className="df-grow" />
        <button
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={loadSchema}
          disabled={!sourceId || schemaLoading}
        >
          {schemaLoading ? "⟳" : "⌗"} browse schema
        </button>
      </div>

      <div className="df-workspace__editor-body">
        <textarea
          className="df-textarea"
          value={statement}
          onChange={(e) => onStatementChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="SELECT * FROM …&#10;⌘/Ctrl + Enter to execute"
          spellCheck={false}
          aria-label="SQL query input"
        />
      </div>

      <div className="df-workspace__actions">
        <button
          className="df-btn df-btn--primary"
          onClick={onExecute}
          disabled={!canExecute}
        >
          {loading ? "⟳ executing…" : "▸ execute"}
        </button>
        <span className="df-mono df-subtle" style={{ fontSize: "var(--df-fs-xs)" }}>
          ⌘/Ctrl + ↵
        </span>
      </div>

      {showSchema ? (
        <SchemaPanel resources={schema} loading={schemaLoading} error={schemaError} onClose={() => setShowSchema(false)} />
      ) : null}
    </div>
  );
}

interface SchemaPanelProps {
  resources: ResourceMeta[] | null;
  loading: boolean;
  error: SidecarError | null;
  onClose: () => void;
}

function SchemaPanel({ resources, loading, error, onClose }: SchemaPanelProps) {
  return (
    <div className="df-card df-fade-in">
      <div className="df-card__head">
        <span className="df-card__title">schema</span>
        <button className="df-btn df-btn--ghost df-btn--sm" onClick={onClose}>
          ✕ close
        </button>
      </div>
      <div className="df-card__body">
        {error ? <ErrorBanner error={error} /> : null}
        {loading ? <div className="df-muted">Loading schema…</div> : null}
        {!loading && !error && resources
          ? resources.map((r) => (
              <div key={r.name} style={{ marginBottom: "var(--df-space-3)" }}>
                <div className="df-mono" style={{ fontSize: "var(--df-fs-sm)", color: "var(--df-accent)" }}>
                  {r.name}
                </div>
                <div className="df-row df-row--wrap" style={{ gap: "var(--df-space-1)", marginTop: "var(--df-space-1)" }}>
                  {r.columns.map((c) => (
                    <span key={c.name} className="df-badge">
                      {c.name}
                      <span className="df-subtle">:{c.type}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))
          : null}
        {!loading && !error && resources && resources.length === 0 ? (
          <div className="df-muted">No resources discovered.</div>
        ) : null}
      </div>
    </div>
  );
}
