import { useRef, useState } from "react";
import { api, SidecarError } from "@/api/client";
import type { ResourceMeta } from "@/api/types";
import { ErrorBanner } from "./ErrorBanner";

interface QueryEditorProps {
  sourceId: string;
  statement: string;
  onStatementChange: (sql: string) => void;
  onExecute: () => void;
  loading: boolean;
  schema?: ResourceMeta[] | null;
}

interface SuggestionItem {
  label: string;
  insertText: string;
  detail?: string;
}

function getSuggestions(
  text: string,
  cursorPos: number,
  schema: ResourceMeta[] | null,
): SuggestionItem[] {
  if (!schema || schema.length === 0) return [];

  const before = text.slice(0, cursorPos);

  // "table.column" pattern — dot after a word
  const dotMatch = before.match(/(\w+)\.(\w*)$/);
  if (dotMatch) {
    const tableName = dotMatch[1];
    const table = schema.find(
      (r) => r.name.toLowerCase() === tableName.toLowerCase(),
    );
    if (table) {
      return table.columns.map((col) => ({
        label: col.name,
        insertText: col.name,
        detail: col.type,
      }));
    }
    return [];
  }

  // FROM / JOIN keyword pattern
  const keywordMatch = before.match(/\b(FROM|JOIN)\s+(\w*)$/i);
  if (keywordMatch) {
    return schema.map((r) => ({
      label: r.name,
      insertText: r.name,
      detail: `${r.columns.length} columns`,
    }));
  }

  return [];
}

function formatSql(sql: string): string {
  const keywords = [
    "SELECT",
    "FROM",
    "WHERE",
    "AND",
    "OR",
    "GROUP BY",
    "ORDER BY",
    "LIMIT",
    "JOIN",
    "LEFT JOIN",
    "RIGHT JOIN",
    "INNER JOIN",
    "ON",
    "INSERT INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE FROM",
  ];
  let result = sql;
  for (const kw of keywords) {
    const regex = new RegExp(`\\b${kw}\\b`, "gi");
    result = result.replace(regex, kw);
  }
  // Newline after SELECT, indent columns
  result = result.replace(/SELECT\s+/i, "SELECT\n  ");
  // Newline + indent after commas
  result = result.replace(/,\s+/g, ",\n  ");
  // Newline before major clauses
  result = result.replace(/\s+FROM\s+/gi, "\nFROM\n  ");
  result = result.replace(/\s+WHERE\s+/gi, "\nWHERE\n  ");
  result = result.replace(/\s+GROUP BY\s+/gi, "\nGROUP BY\n  ");
  result = result.replace(/\s+ORDER BY\s+/gi, "\nORDER BY\n  ");
  result = result.replace(/\s+LIMIT\s+/gi, "\nLIMIT\n  ");
  return result.trim();
}

export function QueryEditor({
  sourceId,
  statement,
  onStatementChange,
  onExecute,
  loading,
  schema,
}: QueryEditorProps) {
  const [localSchema, setLocalSchema] = useState<ResourceMeta[] | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<SidecarError | null>(null);
  const [showSchema, setShowSchema] = useState(false);

  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([]);
  const [activeSuggestion, setActiveSuggestion] = useState(0);
  const [showSuggestions, setShowSuggestions] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  const autocompleteSchema = schema ?? localSchema;

  const lineCount = statement.split("\n").length;
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1);

  const loadSchema = async () => {
    if (!sourceId) return;
    setSchemaLoading(true);
    setSchemaError(null);
    try {
      const data = await api.getSchema(sourceId);
      setLocalSchema(data.resources);
      setShowSchema(true);
    } catch (err) {
      setLocalSchema(null);
      setSchemaError(err instanceof SidecarError ? err : null);
      setShowSchema(true);
    } finally {
      setSchemaLoading(false);
    }
  };

  const handleFormat = () => {
    if (statement.trim().length === 0) return;
    onStatementChange(formatSql(statement));
  };

  const handleScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
    if (gutterRef.current) {
      gutterRef.current.scrollTop = e.currentTarget.scrollTop;
    }
  };

  const dismissSuggestions = () => {
    setShowSuggestions(false);
    setSuggestions([]);
    setActiveSuggestion(0);
  };

  const insertSuggestion = (item: SuggestionItem) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const cursor = ta.selectionStart;
    const before = statement.slice(0, cursor);

    const dotMatch = before.match(/(\w+)\.(\w*)$/);
    const kwMatch = before.match(/\b(FROM|JOIN)\s+(\w*)$/i);

    let replaceStart: number;
    let insertText: string;

    if (dotMatch) {
      replaceStart = cursor - dotMatch[2].length;
      insertText = item.insertText;
    } else if (kwMatch) {
      replaceStart = cursor - kwMatch[2].length;
      insertText = item.insertText;
    } else {
      replaceStart = cursor;
      insertText = item.insertText;
    }

    const next =
      statement.slice(0, replaceStart) + insertText + statement.slice(cursor);
    onStatementChange(next);
    const newCursor = replaceStart + insertText.length;
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.selectionStart = textareaRef.current.selectionEnd =
          newCursor;
        textareaRef.current.focus();
      }
    });
    dismissSuggestions();
  };

  const updateSuggestions = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    const cursor = ta.selectionStart;
    const items = getSuggestions(statement, cursor, autocompleteSchema);
    if (items.length > 0) {
      setSuggestions(items);
      setActiveSuggestion(0);
      setShowSuggestions(true);
    } else {
      dismissSuggestions();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showSuggestions && suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveSuggestion((i) => (i + 1) % suggestions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveSuggestion(
          (i) => (i - 1 + suggestions.length) % suggestions.length,
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insertSuggestion(suggestions[activeSuggestion]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        dismissSuggestions();
        return;
      }
    }

    if (e.ctrlKey && e.code === "Space") {
      e.preventDefault();
      updateSuggestions();
      return;
    }

    // Execute
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      if (canExecute) onExecute();
      return;
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

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onStatementChange(e.target.value);
    requestAnimationFrame(updateSuggestions);
  };

  const canExecute =
    Boolean(sourceId) && statement.trim().length > 0 && !loading;

  const cursorLine = statement
    .slice(0, textareaRef.current?.selectionStart ?? 0)
    .split("\n").length;
  const dropdownTop = `calc(var(--df-space-4) + ${cursorLine - 1} * 1.55rem)`;

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
        <button
          className="df-btn df-btn--ghost df-btn--sm"
          onClick={handleFormat}
          disabled={statement.trim().length === 0}
          title="Format SQL"
        >
          ⊞ format
        </button>
      </div>

      <div
        className="df-workspace__editor-body"
        style={{ position: "relative" }}
      >
        <div
          ref={gutterRef}
          aria-hidden="true"
          style={{
            width: "40px",
            flex: "0 0 40px",
            background: "var(--df-bg-elevated)",
            color: "var(--df-fg-subtle)",
            fontFamily: "var(--df-font-mono)",
            fontSize: "var(--df-fs-xs)",
            lineHeight: "var(--df-lh-base)",
            textAlign: "right",
            padding:
              "var(--df-space-4) var(--df-space-2) var(--df-space-4) 0",
            userSelect: "none",
            border: "1px solid var(--df-border-strong)",
            borderRight: "1px solid var(--df-border)",
            borderRadius: "var(--df-radius-md) 0 0 var(--df-radius-md)",
            overflow: "hidden",
            whiteSpace: "pre",
          }}
        >
          {lineNumbers.map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
        <textarea
          ref={textareaRef}
          className="df-textarea"
          style={{ borderRadius: "0 var(--df-radius-md) var(--df-radius-md) 0" }}
          value={statement}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onScroll={handleScroll}
          onBlur={() => {
            setTimeout(() => dismissSuggestions(), 150);
          }}
          placeholder="SELECT * FROM …&#10;⌘/Ctrl + Enter to execute"
          spellCheck={false}
          aria-label="SQL query input"
        />

        {showSuggestions && suggestions.length > 0 ? (
          <div
            style={{
              position: "absolute",
              top: dropdownTop,
              left: "40px",
              width: "240px",
              maxHeight: "200px",
              overflowY: "auto",
              background: "var(--df-bg-elevated)",
              border: "1px solid var(--df-border-strong)",
              borderRadius: "var(--df-radius-md)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
              zIndex: 10,
              padding: "var(--df-space-1) 0",
            }}
          >
            {suggestions.map((item, idx) => (
              <div
                key={`${item.label}-${idx}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertSuggestion(item);
                }}
                onMouseEnter={() => setActiveSuggestion(idx)}
                style={{
                  padding:
                    "var(--df-space-1) var(--df-space-3)",
                  cursor: "pointer",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "var(--df-space-2)",
                  background:
                    idx === activeSuggestion
                      ? "var(--df-accent-soft)"
                      : "transparent",
                }}
              >
                <span
                  className="df-mono"
                  style={{ fontSize: "var(--df-fs-sm)" }}
                >
                  {item.label}
                </span>
                {item.detail ? (
                  <span
                    className="df-subtle"
                    style={{ fontSize: "var(--df-fs-xs)" }}
                  >
                    {item.detail}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="df-workspace__actions">
        <button
          className="df-btn df-btn--primary"
          onClick={onExecute}
          disabled={!canExecute}
        >
          {loading ? "⟳ executing…" : "▸ execute"}
        </button>
        <span
          className="df-mono df-subtle"
          style={{ fontSize: "var(--df-fs-xs)" }}
        >
          ⌘/Ctrl + ↵
        </span>
      </div>

      {showSchema ? (
        <SchemaPanel
          resources={localSchema}
          loading={schemaLoading}
          error={schemaError}
          onClose={() => setShowSchema(false)}
        />
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
                <div
                  className="df-mono"
                  style={{
                    fontSize: "var(--df-fs-sm)",
                    color: "var(--df-accent)",
                  }}
                >
                  {r.name}
                </div>
                <div
                  className="df-row df-row--wrap"
                  style={{
                    gap: "var(--df-space-1)",
                    marginTop: "var(--df-space-1)",
                  }}
                >
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