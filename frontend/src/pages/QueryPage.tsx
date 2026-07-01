import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@/context/QueryContext";
import { useShellStore } from "@/store/shell";
import { api } from "@/api/client";
import type { ResourceMeta } from "@/api/types";
import { QueryEditor } from "@/components/QueryEditor";
import { SchemaPanel } from "@/components/SchemaPanel";
import { ResultGrid } from "@/components/ResultGrid";
import { ResultTabs, type PinnedResult } from "@/components/ResultTabs";
import { SaveQueryModal } from "@/components/SaveQueryModal";
import { ErrorBanner } from "@/components/ErrorBanner";
import { EmptyState } from "@/components/EmptyState";

const MAX_PINS = 5;
const SPLIT_MIN = 0.3;
const SPLIT_MAX = 0.8;
const SPLIT_DEFAULT = 0.6;

const pageStyle: CSSProperties = {
  height: "100%",
  display: "flex",
  flexDirection: "column",
};

const warningStyle: CSSProperties = {
  padding: "var(--df-space-2) var(--df-space-4)",
  background: "rgba(249, 226, 175, 0.1)",
  color: "var(--df-fg)",
  fontSize: "var(--df-fs-sm)",
  borderBottom: "1px solid var(--df-border)",
};

const splitStyle: CSSProperties = {
  display: "flex",
  height: "100%",
  minHeight: 0,
};

const leftPanelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
};

const dividerStyle: CSSProperties = {
  width: "4px",
  cursor: "col-resize",
  background: "var(--df-border)",
  flexShrink: 0,
  transition: "background var(--df-dur-fast) var(--df-ease)",
};

const rightPanelStyle: CSSProperties = {
  minWidth: 0,
};

const pinBarStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  padding: "var(--df-space-1) var(--df-space-2)",
};

const pinButtonStyle: CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--df-fg-subtle)",
  cursor: "pointer",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
  padding: "var(--df-space-1) var(--df-space-2)",
  borderRadius: "var(--df-radius-sm)",
  transition: "color var(--df-dur-fast) var(--df-ease)",
};

const resultWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  flex: 1,
  minHeight: 0,
};

export function QueryPage() {
  const { statement, result, loading, error, setStatement, execute, clearResult, setSourceId } =
    useQuery();
  const selectedSources = useShellStore((s) => s.selectedSources);

  const sourceId = selectedSources[0] ?? "";

  useEffect(() => {
    setSourceId(sourceId);
  }, [sourceId, setSourceId]);

  const [schema, setSchema] = useState<ResourceMeta[] | null>(null);
  const [splitRatio, setSplitRatio] = useState<number>(SPLIT_DEFAULT);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [pinnedResults, setPinnedResults] = useState<PinnedResult[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareTabId, setCompareTabId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    if (!sourceId) {
      setSchema(null);
      return;
    }
    let cancelled = false;
    api
      .getSchema(sourceId)
      .then((data) => {
        if (!cancelled) setSchema(data.resources);
      })
      .catch(() => {
        if (!cancelled) setSchema(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (statement.trim().length > 0) {
          setSaveModalOpen(true);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [statement]);

  const handleMouseDown = () => {
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const ratio = (e.clientX - rect.left) / rect.width;
      setSplitRatio(Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, ratio)));
    };
    const onMouseUp = () => {
      draggingRef.current = false;
      document.body.style.cursor = "";
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const handleInsert = (text: string) => {
    setStatement(statement ? `${statement}\n${text}` : text);
  };

  const handlePinResult = () => {
    if (!result) return;
    if (pinnedResults.length >= MAX_PINS) return;
    const newPin: PinnedResult = {
      id: `pin-${Date.now()}`,
      label: `Result ${pinnedResults.length + 1}`,
      result,
      timestamp: Date.now(),
    };
    setPinnedResults((prev) => [...prev, newPin]);
    setActiveTabId(newPin.id);
  };

  const handleCloseTab = (id: string) => {
    setPinnedResults((prev) => prev.filter((p) => p.id !== id));
    if (activeTabId === id) setActiveTabId(null);
    if (compareTabId === id) setCompareTabId(null);
  };

  const handleToggleCompareTab = (id: string) => {
    setCompareTabId((prev) => (prev === id ? null : id));
  };

  const handleToggleCompareMode = () => {
    setCompareMode((prev) => {
      const next = !prev;
      if (!next) setCompareTabId(null);
      return next;
    });
  };

  const activePinned = activeTabId
    ? pinnedResults.find((p) => p.id === activeTabId) ?? null
    : null;
  const displayResult = activePinned?.result ?? result;

  const showPinnedView = Boolean(activePinned);
  const showLivePinBar = !activePinned && result && !error;
  const showEmpty = !displayResult && !loading && !error;

  return (
    <div style={pageStyle}>
      {selectedSources.length === 0 ? (
        <div style={warningStyle}>
          Select a data source in the sidebar to start querying.
        </div>
      ) : null}

      <div ref={containerRef} style={splitStyle}>
        {/* Left panel: result tabs + editor + results */}
        <div
          style={{
            ...leftPanelStyle,
            flex: `${splitRatio} 1 0`,
          }}
        >
          {pinnedResults.length > 0 ? (
            <ResultTabs
              pinnedResults={pinnedResults}
              activeTabId={activeTabId}
              onSelectTab={setActiveTabId}
              onCloseTab={handleCloseTab}
              compareMode={compareMode}
              compareTabId={compareTabId}
              onToggleCompareTab={handleToggleCompareTab}
              onToggleCompareMode={handleToggleCompareMode}
              maxTabs={MAX_PINS}
            />
          ) : null}

          <QueryEditor
            sourceId={sourceId}
            statement={statement}
            onStatementChange={setStatement}
            onExecute={execute}
            loading={loading}
            schema={schema}
          />

          {error ? (
            <div className="df-fade-in">
              <ErrorBanner error={error} onDismiss={clearResult} />
            </div>
          ) : null}

          {showLivePinBar ? (
            <div style={pinBarStyle}>
              <button
                type="button"
                className="df-btn df-btn--ghost df-btn--sm"
                style={pinButtonStyle}
                onClick={handlePinResult}
                disabled={pinnedResults.length >= MAX_PINS}
              >
                📌 Pin Result
              </button>
            </div>
          ) : null}

          {showPinnedView && displayResult ? (
            <div style={resultWrapStyle}>
              <ResultGrid result={displayResult} />
            </div>
          ) : result && !error ? (
            <div style={resultWrapStyle}>
              <ResultGrid result={result} />
            </div>
          ) : showEmpty ? (
            <EmptyState
              glyph="≋"
              title="No results yet"
              sub="Write a query and hit execute."
            />
          ) : null}
        </div>

        {/* Resizable divider */}
        <div
          style={dividerStyle}
          onMouseDown={handleMouseDown}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--df-border-strong)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--df-border)";
          }}
        />

        {/* Right panel: schema */}
        <div
          style={{
            ...rightPanelStyle,
            flex: `${1 - splitRatio} 1 0`,
          }}
        >
          <SchemaPanel sourceIds={selectedSources} onInsert={handleInsert} />
        </div>
      </div>

      <SaveQueryModal
        isOpen={saveModalOpen}
        onClose={() => setSaveModalOpen(false)}
        statement={statement}
        sourceIds={selectedSources}
      />
    </div>
  );
}
