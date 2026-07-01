import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useShellStore } from "@/store/shell";
import { api } from "@/api/client";
import { datasetsApi, type Dataset } from "@/api/datasets";
import type { AppMode } from "@/store/shell";
import type { SourceSummary, SourceHealth } from "@/api/types";

interface ModeEntry {
  mode: AppMode;
  label: string;
  icon: string;
}

const MODES: ModeEntry[] = [
  { mode: "explore", label: "Explore", icon: "▌" },
  { mode: "monitor", label: "Monitor", icon: "▣" },
  { mode: "query", label: "Query", icon: "▸" },
];

const MONITOR_AGENT_COUNT = 3;
const COLLAPSE_BREAKPOINT = 1024;

function sourceTypeColor(type: string): string {
  const t = type.toLowerCase();
  if (t === "mysql") return "var(--df-yellow)";
  if (t === "postgresql" || t === "postgres") return "var(--df-blue)";
  if (t === "http" || t === "http_api" || t === "http-api") return "var(--df-teal)";
  return "var(--df-purple)";
}

function healthColor(health: SourceHealth): string {
  if (health === "healthy") return "var(--df-green)";
  if (health === "unhealthy") return "var(--df-red)";
  return "var(--df-yellow)";
}

export function Sidebar() {
  const activeMode = useShellStore((s) => s.activeMode);
  const selectedSources = useShellStore((s) => s.selectedSources);
  const collapsed = useShellStore((s) => s.sidebarCollapsed);
  const switchMode = useShellStore((s) => s.switchMode);
  const toggleSource = useShellStore((s) => s.toggleSource);
  const setSidebarCollapsed = useShellStore((s) => s.setSidebarCollapsed);
  const theme = useShellStore((s) => s.theme);
  const toggleTheme = useShellStore((s) => s.toggleTheme);

  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    api
      .listSources()
      .then((data) => {
        if (!cancelled) {
          setSources(data);
          setLoadError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Failed to load sources");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    datasetsApi.list().then((data) => {
      if (!cancelled) setDatasets(data);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const onResize = () => {
      const shouldCollapse = window.innerWidth <= COLLAPSE_BREAKPOINT;
      setSidebarCollapsed(shouldCollapse);
    };
    onResize(); // sync on mount
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [setSidebarCollapsed]);

  const multiSelected = selectedSources.length >= 2;

  return (
    <aside className="df-sidebar" data-collapsed={collapsed ? "true" : "false"}>
      <div className="df-brand">
        <span className="df-brand__mark">deepferry</span>
        {!collapsed && <span className="df-brand__tag">data ferry</span>}
      </div>

      <nav className="df-nav" aria-label="Workspace modes">
        <div className="df-nav__label">{collapsed ? "" : "Mode"}</div>
        {MODES.map((entry) => {
          const isActive = activeMode === entry.mode;
          return (
            <button
              key={entry.mode}
              type="button"
              className={`df-nav__link${isActive ? " is-active" : ""}`}
              onClick={() => switchMode(entry.mode)}
              title={entry.label}
              aria-pressed={isActive}
            >
              <span className="df-nav__icon">{entry.icon}</span>
              {!collapsed && <span>{entry.label}</span>}
              {entry.mode === "monitor" && !collapsed && (
                <span
                  style={{
                    marginLeft: "auto",
                    fontFamily: "var(--df-font-mono)",
                    fontSize: "var(--df-fs-xs)",
                    color: "var(--df-fg-subtle)",
                    background: "var(--df-surface0)",
                    border: "1px solid var(--df-border)",
                    borderRadius: "var(--df-radius-pill)",
                    padding: "0 var(--df-space-2)",
                    lineHeight: 1.4,
                  }}
                >
                  {MONITOR_AGENT_COUNT}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <nav className="df-nav" aria-label="Data sources" style={{ flex: 1 }}>
        <div className="df-nav__label">{collapsed ? "" : "Data Sources"}</div>

        {loadError && !collapsed && (
          <div
            style={{
              padding: "var(--df-space-2) var(--df-space-3)",
              fontSize: "var(--df-fs-xs)",
              color: "var(--df-danger)",
            }}
          >
            {loadError}
          </div>
        )}

        {sources.map((source) => {
          const isSelected = selectedSources.includes(source.id);
          return (
            <label
              key={source.id}
              className={`df-nav__link${isSelected ? " is-active" : ""}`}
              style={{ cursor: "pointer", userSelect: "none" }}
              title={source.name}
            >
              {/* Multi-select checkbox */}
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => toggleSource(source.id)}
                style={{
                  accentColor: "var(--df-accent)",
                  width: "0.85em",
                  height: "0.85em",
                  flexShrink: 0,
                }}
                aria-label={`Toggle ${source.name}`}
              />

              {/* Type color dot */}
              <span
                aria-hidden="true"
                style={{
                  width: "0.55em",
                  height: "0.55em",
                  borderRadius: "var(--df-radius-pill)",
                  background: sourceTypeColor(source.type),
                  flexShrink: 0,
                  boxShadow: `0 0 6px ${sourceTypeColor(source.type)}`,
                }}
              />

              {!collapsed && (
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {source.name}
                </span>
              )}

              {/* Health dot */}
              {!collapsed && (
                <span
                  aria-label={`Health: ${source.health}`}
                  title={`Health: ${source.health}`}
                  style={{
                    width: "0.45em",
                    height: "0.45em",
                    borderRadius: "var(--df-radius-pill)",
                    background: healthColor(source.health),
                    flexShrink: 0,
                    marginLeft: "auto",
                  }}
                />
              )}
            </label>
          );
        })}
      </nav>

      {multiSelected && !collapsed && (
        <div
          style={{
            margin: "0 var(--df-space-3) var(--df-space-2)",
            padding: "var(--df-space-2) var(--df-space-3)",
            fontFamily: "var(--df-font-mono)",
            fontSize: "var(--df-fs-xs)",
            color: "var(--df-teal)",
            background: "rgba(148, 226, 213, 0.08)",
            border: "1px solid rgba(148, 226, 213, 0.2)",
            borderRadius: "var(--df-radius-sm)",
          }}
        >
          Selected {selectedSources.length} sources — cross-source ready
        </div>
      )}

      {datasets.length > 0 && (
        <nav className="df-nav" aria-label="Datasets">
          <div className="df-nav__label">{collapsed ? "" : "Datasets"}</div>
          {datasets.map((ds) => (
            <button
              key={ds.id}
              type="button"
              className="df-nav__link"
              onClick={() => navigate(`/datasets/${ds.id}`)}
              title={ds.name}
              style={{
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "var(--df-space-2)",
              }}
            >
              <span className="df-nav__icon">⊞</span>
              {!collapsed && (
                <>
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ds.name}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--df-font-mono)",
                      fontSize: "var(--df-fs-xs)",
                      color: "var(--df-fg-subtle)",
                      flexShrink: 0,
                    }}
                  >
                    v{ds.latest_version}
                  </span>
                </>
              )}
            </button>
          ))}
        </nav>
      )}

      <div className="df-sidebar__footer">
        {collapsed ? (
          <span title="v0.1.0 · m3 · settings">v0.1</span>
        ) : (
          <>
            v0.1.0 · m3
            <br />
            <a
              href="#settings"
              style={{
                color: "var(--df-fg-subtle)",
                textDecoration: "none",
                borderBottom: "1px dashed var(--df-border-strong)",
              }}
            >
              settings
            </a>
            <button
              type="button"
              onClick={toggleTheme}
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              style={{
                background: "none",
                border: "1px solid var(--df-border)",
                borderRadius: "var(--df-radius-sm)",
                padding: "2px var(--df-space-2)",
                cursor: "pointer",
                color: "var(--df-fg-subtle)",
                fontSize: "var(--df-fs-xs)",
                marginLeft: "var(--df-space-2)",
              }}
            >
              {theme === "dark" ? "☀" : "☾"}
            </button>
          </>
        )}
      </div>
    </aside>
  );
}