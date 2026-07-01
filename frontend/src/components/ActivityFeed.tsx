import { useMemo, type CSSProperties, type ReactElement } from "react";
import { List } from "react-window";
import type { FeedEvent, FilterState } from "@/api/agents";
import type { ConnectionState } from "@/api/ws";

export interface ActivityFeedProps {
  events: FeedEvent[];
  filters: FilterState;
  onSelect: (event: FeedEvent) => void;
  connectionStatus: ConnectionState;
  selectedEventId?: string | null;
}

const ROW_HEIGHT = 64;
const LIST_HEIGHT = 400;

// ── Status → color ─────────────────────────────────────────
function statusDotColor(status: FeedEvent["status"]): string {
  switch (status) {
    case "running":
      return "var(--df-yellow)";
    case "ok":
      return "var(--df-green)";
    case "error":
      return "var(--df-red)";
  }
}

// ── Source badge color by type ─────────────────────────────
function sourceBadgeColor(sourceId: string): string {
  if (sourceId.startsWith("mysql")) return "var(--df-yellow)";
  if (sourceId.startsWith("postgres")) return "var(--df-blue)";
  if (sourceId.startsWith("http")) return "var(--df-teal)";
  return "var(--df-overlay1)";
}

// ── Row style factory (selected highlight) ──────────────────
function rowStyle(isSelected: boolean): CSSProperties {
  const base: CSSProperties = {
    width: "100%",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    gap: "var(--df-space-1)",
    padding: "0 var(--df-space-3)",
    cursor: "pointer",
    textAlign: "left",
    color: "inherit",
    font: "inherit",
    borderLeft: "2px solid transparent",
    background: "transparent",
  };
  if (isSelected) {
    return {
      ...base,
      background: "var(--df-accent-soft)",
      borderLeft: "2px solid var(--df-accent)",
    };
  }
  return base;
}

// ── Connection banner ──────────────────────────────────────
function ConnectionBanner({ status }: { status: ConnectionState }) {
  if (status === "connected") return null;

  let label: string;
  let dotColor: string;
  let pulse = false;

  switch (status) {
    case "connecting":
      label = "Connecting to agent feed…";
      dotColor = "var(--df-yellow)";
      break;
    case "reconnecting":
      label = "Reconnecting…";
      dotColor = "var(--df-yellow)";
      pulse = true;
      break;
    case "disconnected":
      label = "Disconnected from agent feed";
      dotColor = "var(--df-red)";
      break;
  }

  const dotStyle: CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: dotColor,
    flexShrink: 0,
    animation: pulse ? "df-pulse 1.4s ease-in-out infinite" : undefined,
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--df-space-2)",
        padding: "var(--df-space-2) var(--df-space-3)",
        background: "var(--df-bg-elevated)",
        borderBottom: "1px solid var(--df-border)",
        fontSize: "var(--df-fs-xs)",
        color: "var(--df-fg-subtle)",
      }}
    >
      <span style={dotStyle} />
      <span>{label}</span>
    </div>
  );
}

// ── Status dot ─────────────────────────────────────────────
function StatusDot({ status }: { status: FeedEvent["status"] }) {
  const pulse = status === "running";
  return (
    <span
      style={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: statusDotColor(status),
        flexShrink: 0,
        animation: pulse ? "df-pulse 1.4s ease-in-out infinite" : undefined,
      }}
    />
  );
}

// ── Source pill ────────────────────────────────────────────
function SourcePill({ sourceId }: { sourceId: string }) {
  const color = sourceBadgeColor(sourceId);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "0 var(--df-space-2)",
        borderRadius: "var(--df-radius-pill)",
        fontSize: "var(--df-fs-xs)",
        fontFamily: "var(--df-font-mono)",
        color,
        background: `color-mix(in srgb, ${color} 14%, transparent)`,
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
        whiteSpace: "nowrap",
      }}
    >
      {sourceId}
    </span>
  );
}

// ── Tag pill ───────────────────────────────────────────────
function TagPill({ tag }: { tag: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "0 var(--df-space-2)",
        borderRadius: "var(--df-radius-pill)",
        fontSize: "var(--df-fs-xs)",
        fontFamily: "var(--df-font-mono)",
        color: "var(--df-crust)",
        background: "var(--df-purple)",
        whiteSpace: "nowrap",
      }}
    >
      {tag}
    </span>
  );
}

// ── Row renderer (react-window v2 API) ─────────────────────
interface RowExtraProps {
  filteredEvents: FeedEvent[];
  selectedEventId: string | null | undefined;
  onSelect: (event: FeedEvent) => void;
}

interface RowProps {
  index: number;
  style: CSSProperties;
  ariaAttributes: {
    "aria-posinset": number;
    "aria-setsize": number;
    role: "listitem";
  };
}

function Row({
  index,
  style,
  ariaAttributes,
  filteredEvents,
  selectedEventId,
  onSelect,
}: RowProps & RowExtraProps): ReactElement | null {
  const event = filteredEvents[index];
  if (!event) return null;

  const isSelected = event.id === selectedEventId;

  return (
    <div style={style} {...ariaAttributes}>
      <button
        type="button"
        onClick={() => onSelect(event)}
        style={rowStyle(isSelected)}
      >
        {/* Line 1: dot + agent + duration */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--df-space-2)",
            width: "100%",
          }}
        >
          <StatusDot status={event.status} />
          <span
            style={{
              color: "var(--df-fg)",
              fontSize: "var(--df-fs-sm)",
              fontWeight: 500,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              flex: "1 1 auto",
              minWidth: 0,
            }}
          >
            {event.agent}
          </span>
          <span
            style={{
              color: "var(--df-fg-subtle)",
              fontSize: "var(--df-fs-xs)",
              fontFamily: "var(--df-font-mono)",
              flexShrink: 0,
            }}
          >
            {event.duration_ms === null ? "…" : `${event.duration_ms}ms`}
          </span>
        </div>

        {/* Line 2: SQL preview + source badges + tags */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--df-space-2)",
            width: "100%",
            paddingLeft: "var(--df-space-4)",
          }}
        >
          <span
            title={event.statement}
            style={{
              color: "var(--df-fg-subtle)",
              fontSize: "var(--df-fs-xs)",
              fontFamily: "var(--df-font-mono)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flex: "1 1 auto",
              minWidth: 0,
            }}
          >
            {event.statement}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--df-space-1)",
              flexShrink: 0,
            }}
          >
            {event.source_ids.map((sid) => (
              <SourcePill key={sid} sourceId={sid} />
            ))}
            {event.tags.map((tag) => (
              <TagPill key={tag} tag={tag} />
            ))}
          </span>
        </div>
      </button>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────
export function ActivityFeed({
  events,
  filters,
  onSelect,
  connectionStatus,
  selectedEventId = null,
}: ActivityFeedProps) {
  const filteredEvents = useMemo(() => {
    return events.filter((event) => {
      if (filters.agent !== "all" && event.agent !== filters.agent) return false;
      if (filters.status !== "all" && event.status !== filters.status) return false;
      if (
        filters.source !== "all" &&
        !event.source_ids.includes(filters.source)
      )
        return false;
      return true;
    });
  }, [events, filters]);

  const rowProps: RowExtraProps = {
    filteredEvents,
    selectedEventId,
    onSelect,
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "var(--df-bg)",
      }}
    >
      <ConnectionBanner status={connectionStatus} />

      {filteredEvents.length === 0 ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "1 1 auto",
            minHeight: 0,
            color: "var(--df-fg-subtle)",
            fontSize: "var(--df-fs-sm)",
            padding: "var(--df-space-8)",
            textAlign: "center",
          }}
        >
          No activity matches your filters
        </div>
      ) : (
        <div style={{ flex: "1 1 auto", minHeight: 0 }}>
          <List<RowExtraProps>
            rowCount={filteredEvents.length}
            rowHeight={ROW_HEIGHT}
            rowComponent={Row}
            rowProps={rowProps}
            style={{ height: LIST_HEIGHT, width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}