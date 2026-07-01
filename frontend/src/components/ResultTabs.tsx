import { type CSSProperties } from "react";
import type { QueryResult } from "@/api/types";

export interface PinnedResult {
  id: string;
  label: string;
  result: QueryResult;
  timestamp: number;
}

export interface ResultTabsProps {
  pinnedResults: PinnedResult[];
  activeTabId: string | null;
  onSelectTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onCompare?: (id1: string, id2: string) => void;
  compareMode: boolean;
  compareTabId: string | null;
  onToggleCompareTab: (id: string) => void;
  onToggleCompareMode?: () => void;
  maxTabs?: number;
}

const tabBarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "2px",
  borderBottom: "1px solid var(--df-border)",
  padding: "0 var(--df-space-2)",
};

const baseTabStyle: CSSProperties = {
  padding: "var(--df-space-2) var(--df-space-3)",
  border: "1px solid transparent",
  borderBottom: "none",
  borderRadius: "var(--df-radius) var(--df-radius) 0 0",
  background: "transparent",
  color: "var(--df-fg-subtle)",
  cursor: "pointer",
  fontSize: "var(--df-fs-sm)",
  fontFamily: "var(--df-font-sans)",
  display: "flex",
  alignItems: "center",
  gap: "var(--df-space-2)",
};

const activeTabStyle: CSSProperties = {
  ...baseTabStyle,
  background: "var(--df-bg-elevated)",
  color: "var(--df-fg)",
  borderColor: "var(--df-border)",
};

const compareSelectedStyle: CSSProperties = {
  ...activeTabStyle,
  borderTop: "2px solid var(--df-purple)",
};

const closeButtonStyle: CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--df-fg-subtle)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  padding: 0,
  lineHeight: 1,
  display: "inline-flex",
  alignItems: "center",
};

const addButtonStyle: CSSProperties = {
  ...baseTabStyle,
  color: "var(--df-fg-subtle)",
};

const addButtonDisabledStyle: CSSProperties = {
  ...addButtonStyle,
  cursor: "not-allowed",
  opacity: 0.4,
};

const compareButtonStyle: CSSProperties = {
  marginLeft: "auto",
  padding: "var(--df-space-1) var(--df-space-3)",
  background: "transparent",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  color: "var(--df-fg)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  fontFamily: "var(--df-font-sans)",
};

const compareButtonActiveStyle: CSSProperties = {
  ...compareButtonStyle,
  background: "var(--df-accent-soft)",
  borderColor: "var(--df-accent-line)",
  color: "var(--df-accent)",
};

export function ResultTabs({
  pinnedResults,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onCompare,
  compareMode,
  compareTabId,
  onToggleCompareTab,
  onToggleCompareMode,
  maxTabs = 5,
}: ResultTabsProps): JSX.Element {
  const atMax = pinnedResults.length >= maxTabs;

  const handleTabClick = (id: string): void => {
    if (compareMode) {
      onToggleCompareTab(id);
      if (compareTabId && compareTabId !== id) {
        onCompare?.(compareTabId, id);
      }
    } else {
      onSelectTab(id);
    }
  };

  const handleCloseClick = (e: React.MouseEvent, id: string): void => {
    e.stopPropagation();
    onCloseTab(id);
  };

  return (
    <div style={tabBarStyle} role="tablist">
      {pinnedResults.map((tab) => {
        const isActive = tab.id === activeTabId && !compareMode;
        const isCompareSelected = compareMode && tab.id === compareTabId;
        const tabStyle = isCompareSelected
          ? compareSelectedStyle
          : isActive
            ? activeTabStyle
            : baseTabStyle;

        return (
          <div
            key={tab.id}
            role="tab"
            aria-selected={isActive || isCompareSelected}
            style={tabStyle}
            onClick={() => handleTabClick(tab.id)}
          >
            <span>{tab.label}</span>
            <button
              type="button"
              style={closeButtonStyle}
              onClick={(e) => handleCloseClick(e, tab.id)}
              aria-label={`Close ${tab.label}`}
            >
              ✕
            </button>
          </div>
        );
      })}

      <div
        style={atMax ? addButtonDisabledStyle : addButtonStyle}
        aria-disabled={atMax}
        title={atMax ? `Maximum ${maxTabs} tabs` : "Pin current result"}
      >
        +
      </div>

      {onCompare && onToggleCompareMode && (
        <button
          type="button"
          style={compareMode ? compareButtonActiveStyle : compareButtonStyle}
          onClick={onToggleCompareMode}
          title="Compare two pinned results"
        >
          ⇄ Compare
        </button>
      )}
    </div>
  );
}