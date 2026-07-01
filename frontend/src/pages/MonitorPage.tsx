import { useState, useEffect, useRef, useCallback, useMemo, type CSSProperties } from "react";
import { useShellStore } from "@/store/shell";
import { agentsApi, type AgentStats, type FeedEvent, type FilterState } from "@/api/agents";
import { connectAgentFeed, type ConnectionState, type AgentFeedHandle } from "@/api/ws";
import { StatsCards } from "@/components/StatsCards";
import { FeedFilters } from "@/components/FeedFilters";
import { ActivityFeed } from "@/components/ActivityFeed";
import { ExecutionDetailPanel } from "@/components/ExecutionDetailPanel";
import { EmptyState } from "@/components/EmptyState";

const DEFAULT_FILTERS: FilterState = {
  agent: "all",
  status: "all",
  source: "all",
};

const MAX_EVENTS = 500;
const STATS_POLL_MS = 10_000;
const SEED_DELAY_MS = 2_000;

const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: 0,
};

const statsWrapperStyle: CSSProperties = {
  flexShrink: 0,
};

const filtersWrapperStyle: CSSProperties = {
  flexShrink: 0,
};

const feedWrapperStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: "hidden",
  position: "relative",
};

const emptyWrapperStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: "var(--df-space-6) var(--df-space-4)",
  gap: "var(--df-space-4)",
};

const emptyContentStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "var(--df-space-4)",
  maxWidth: 560,
  width: "100%",
};

const codeBlockStyle: CSSProperties = {
  background: "var(--df-bg)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  padding: "var(--df-space-3)",
  fontFamily: "var(--df-font-mono)",
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg)",
  whiteSpace: "pre",
  overflowX: "auto",
  width: "100%",
  boxSizing: "border-box",
};

const codeLabelStyle: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  alignSelf: "flex-start",
};

export function MonitorPage() {
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [statsLoading, setStatsLoading] = useState<boolean>(false);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionState>("connecting");
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [selectedEvent, setSelectedEvent] = useState<FeedEvent | null>(null);

  const feedHandleRef = useRef<AgentFeedHandle | null>(null);

  // ── WebSocket connection (on mount) ─────────────────────────
  useEffect(() => {
    const handle = connectAgentFeed(
      (event) => {
        setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
      },
      (state) => setConnectionStatus(state),
    );
    feedHandleRef.current = handle;
    return () => {
      handle.disconnect();
      feedHandleRef.current = null;
    };
  }, []);

  // ── Stats polling (on mount, every 10s) ────────────────────
  useEffect(() => {
    let cancelled = false;
    const fetchStats = async () => {
      setStatsLoading(true);
      try {
        const s = await agentsApi.getStats();
        if (!cancelled) {
          setStats(s);
          setStatsLoading(false);
        }
      } catch {
        if (!cancelled) setStatsLoading(false);
      }
    };
    fetchStats();
    const interval = setInterval(fetchStats, STATS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // ── Seed initial events (mock mode fallback) ───────────────
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        const sessions = await agentsApi.getSessions(10);
        if (cancelled) return;
        setEvents((prev) => (prev.length > 0 ? prev : sessions.slice(0, 10)));
      } catch {
        void 0;
      }
    }, SEED_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // ── Derived data ───────────────────────────────────────────
  const availableAgents = useMemo<string[]>(
    () => [...new Set(events.map((e) => e.agent))],
    [events],
  );

  const availableSources = useMemo<string[]>(
    () => [...new Set(events.flatMap((e) => e.source_ids))],
    [events],
  );

  // ── Handlers ────────────────────────────────────────────────
  const handleSelect = useCallback((event: FeedEvent) => {
    setSelectedEvent(event);
  }, []);

  const handleClosePanel = useCallback(() => {
    setSelectedEvent(null);
  }, []);

  const handleOpenInQuery = useCallback((sql: string) => {
    // Switch to query mode; SQL handoff is handled by the query page via store
    // (kept simple for now — just switch mode)
    void sql;
    useShellStore.getState().switchMode("query");
  }, []);

  const handleFiltersChange = useCallback((newFilters: FilterState) => {
    setFilters(newFilters);
  }, []);

  // ── Empty state ────────────────────────────────────────────
  const showEmptyState =
    events.length === 0 &&
    connectionStatus !== "connected" &&
    (stats === null || stats.active_agents === 0);

  if (showEmptyState) {
    return (
      <div className="df-page" style={pageStyle}>
        <div style={emptyWrapperStyle}>
          <div style={emptyContentStyle}>
            <EmptyState
              glyph="▣"
              title="No agents connected"
              sub="Connect an MCP client to start seeing live activity."
            />
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: "var(--df-space-2)" }}>
              <span style={codeLabelStyle}>CLI</span>
              <pre style={codeBlockStyle}>{`# Example: connect Claude Code
deepferry mcp-server --transport stdio`}</pre>
            </div>
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: "var(--df-space-2)" }}>
              <span style={codeLabelStyle}>MCP client config</span>
              <pre style={codeBlockStyle}>{`{
  "mcpServers": {
    "deepferry": {
      "command": "deepferry",
      "args": ["mcp-server", "--transport", "stdio"]
    }
  }
}`}</pre>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Main layout ────────────────────────────────────────────
  return (
    <div className="df-page" style={pageStyle}>
      <div style={statsWrapperStyle}>
        <StatsCards stats={stats} loading={statsLoading} />
      </div>
      <div style={filtersWrapperStyle}>
        <FeedFilters
          filters={filters}
          onChange={handleFiltersChange}
          agents={availableAgents}
          sources={availableSources}
        />
      </div>
      <div style={feedWrapperStyle}>
        <ActivityFeed
          events={events}
          filters={filters}
          onSelect={handleSelect}
          connectionStatus={connectionStatus}
          selectedEventId={selectedEvent?.id ?? null}
        />
      </div>
      <ExecutionDetailPanel
        event={selectedEvent}
        onClose={handleClosePanel}
        onOpenInQuery={handleOpenInQuery}
      />
    </div>
  );
}