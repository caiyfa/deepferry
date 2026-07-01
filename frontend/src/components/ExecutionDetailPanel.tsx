import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { FeedEvent, FeedEventStatus, FeedSpan } from "@/api/agents";

interface ExecutionDetailPanelProps {
  event: FeedEvent | null; // null = panel closed
  onClose: () => void;
  onOpenInQuery?: (sql: string) => void;
}

// ── Status badge styling ────────────────────────────────────────────────

const STATUS_BADGE_STYLES: Record<FeedEventStatus, { bg: string; fg: string; label: string }> = {
  running: { bg: "var(--df-accent-soft)", fg: "var(--df-accent)", label: "running" },
  ok: { bg: "rgba(166, 227, 161, 0.14)", fg: "var(--df-green)", label: "ok" },
  error: { bg: "rgba(243, 139, 168, 0.14)", fg: "var(--df-red)", label: "error" },
};

function StatusBadge({ status }: { status: FeedEventStatus }) {
  const s = STATUS_BADGE_STYLES[status];
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: "var(--df-space-1)",
    padding: "2px var(--df-space-2)",
    borderRadius: "var(--df-radius-pill)",
    background: s.bg,
    color: s.fg,
    fontSize: "var(--df-fs-xs)",
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
  };
  return (
    <span style={style}>
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: s.fg,
          boxShadow: status === "running" ? `0 0 6px ${s.fg}` : "none",
        }}
      />
      {s.label}
    </span>
  );
}

// ── Span color by kind ──────────────────────────────────────────────────

function spanColor(kind: string): string {
  switch (kind) {
    case "sql_exec":
      return "var(--df-accent)";
    case "http_request":
      return "var(--df-teal)";
    case "auth":
    case "auth_retry":
      return "var(--df-yellow)";
    case "join":
      return "var(--df-purple)";
    default:
      return "var(--df-fg-subtle)";
  }
}

// ── Timeline waterfall ───────────────────────────────────────────────────

interface TimelineRow {
  span: FeedSpan;
  leftPct: number;
  widthPct: number;
  durationText: string;
}

function buildTimeline(spans: FeedSpan[], eventStartedAt: number): TimelineRow[] {
  if (spans.length === 0) return [];

  const allTimes: number[] = [];
  for (const s of spans) {
    allTimes.push(s.started_at);
    allTimes.push(s.finished_at ?? s.started_at);
  }
  const minTime = Math.min(eventStartedAt, ...allTimes);
  const maxTime = Math.max(...allTimes);
  const totalDuration = Math.max(maxTime - minTime, 1);

  return spans.map((span) => {
    const start = span.started_at - minTime;
    const finish = span.finished_at ?? span.started_at;
    const dur = finish - span.started_at;
    const leftPct = (start / totalDuration) * 100;
    const widthPct = Math.max((dur / totalDuration) * 100, 2);
    return {
      span,
      leftPct,
      widthPct,
      durationText: `${dur}ms`,
    };
  });
}

function TimelineWaterfall({ spans, eventStartedAt }: { spans: FeedSpan[]; eventStartedAt: number }) {
  const rows = buildTimeline(spans, eventStartedAt);
  if (rows.length === 0) return null;

  const containerStyle: CSSProperties = {
    position: "relative",
    height: rows.length * 28,
  };

  return (
    <div style={containerStyle}>
      {rows.map((row, idx) => {
        const isError = row.span.status === "error";
        const color = spanColor(row.span.kind);
        const barStyle: CSSProperties = {
          position: "absolute",
          top: idx * 28,
          left: `${row.leftPct}%`,
          width: `${row.widthPct}%`,
          height: 20,
          borderRadius: "var(--df-radius-sm)",
          overflow: "hidden",
          display: "flex",
          alignItems: "center",
          padding: "0 var(--df-space-2)",
          background: isError ? "rgba(243, 139, 168, 0.18)" : `${color}`,
          border: isError ? `1px solid var(--df-red)` : "none",
          color: isError ? "var(--df-red)" : "var(--df-bg-deep)",
          fontSize: "var(--df-fs-xs)",
          fontWeight: 600,
          whiteSpace: "nowrap",
          textOverflow: "ellipsis",
        };
        return (
          <div key={row.span.id} style={barStyle} title={`${row.span.name} · ${row.durationText}`}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>{row.span.name}</span>
            <span style={{ marginLeft: "var(--df-space-2)", opacity: 0.85 }}>{row.durationText}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Section label ────────────────────────────────────────────────────────

const SECTION_LABEL_STYLE: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: "var(--df-space-2)",
  fontWeight: 600,
};

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={SECTION_LABEL_STYLE}>{children}</div>;
}

// ── Copy button ──────────────────────────────────────────────────────────

function useCopyToClipboard(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const copy = (text: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  };
  return [copied, copy];
}

// ── Main panel ───────────────────────────────────────────────────────────

export function ExecutionDetailPanel({ event, onClose, onOpenInQuery }: ExecutionDetailPanelProps) {
  const [mounted, setMounted] = useState(false);
  const [copied, copy] = useCopyToClipboard();

  // Trigger slide-in transition after first paint
  useEffect(() => {
    if (event) {
      // requestAnimationFrame ensures the initial translateX(100%) is painted
      const raf = requestAnimationFrame(() => setMounted(true));
      return () => cancelAnimationFrame(raf);
    }
    setMounted(false);
  }, [event]);

  // Esc to close
  useEffect(() => {
    if (!event) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [event, onClose]);

  if (!event) return null;

  const overlayStyle: CSSProperties = {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.3)",
    zIndex: 50,
    opacity: mounted ? 1 : 0,
    transition: `opacity var(--df-dur) var(--df-ease)`,
  };

  const panelStyle: CSSProperties = {
    position: "fixed",
    right: 0,
    top: 0,
    bottom: 0,
    width: 480,
    maxWidth: "90vw",
    background: "var(--df-bg-elevated)",
    borderLeft: "1px solid var(--df-border)",
    boxShadow: "-4px 0 16px rgba(0,0,0,0.2)",
    zIndex: 51,
    display: "flex",
    flexDirection: "column",
    transform: mounted ? "translateX(0)" : "translateX(100%)",
    transition: `transform var(--df-dur-slow) var(--df-ease)`,
  };

  const headerStyle: CSSProperties = {
    padding: "var(--df-space-3) var(--df-space-4)",
    borderBottom: "1px solid var(--df-border)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "var(--df-space-3)",
  };

  const contentStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    padding: "var(--df-space-4)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--df-space-4)",
  };

  const sqlPreStyle: CSSProperties = {
    background: "var(--df-bg)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius)",
    padding: "var(--df-space-3)",
    fontFamily: "var(--df-font-mono)",
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg)",
    overflowX: "auto",
    whiteSpace: "pre-wrap",
    lineHeight: "var(--df-lh-base)",
    margin: 0,
  };

  const conversationBoxStyle: CSSProperties = {
    background: "var(--df-bg)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius)",
    padding: "var(--df-space-3)",
    color: "var(--df-fg-muted)",
    fontSize: "var(--df-fs-sm)",
    lineHeight: "var(--df-lh-base)",
  };

  const actionBarStyle: CSSProperties = {
    padding: "var(--df-space-3) var(--df-space-4)",
    borderTop: "1px solid var(--df-border)",
    display: "flex",
    gap: "var(--df-space-2)",
    background: "var(--df-bg-sunken)",
  };

  const primaryBtnStyle: CSSProperties = {
    flex: 1,
    padding: "var(--df-space-2) var(--df-space-3)",
    borderRadius: "var(--df-radius-sm)",
    background: "var(--df-accent)",
    color: "var(--df-bg-deep)",
    fontSize: "var(--df-fs-sm)",
    fontWeight: 600,
    border: "none",
    cursor: "pointer",
  };

  const secondaryBtnStyle: CSSProperties = {
    padding: "var(--df-space-2) var(--df-space-3)",
    borderRadius: "var(--df-radius-sm)",
    background: "var(--df-surface1)",
    color: "var(--df-fg)",
    fontSize: "var(--df-fs-sm)",
    fontWeight: 500,
    border: "1px solid var(--df-border-strong)",
    cursor: "pointer",
    minWidth: 96,
  };

  const closeBtnStyle: CSSProperties = {
    width: 28,
    height: 28,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "var(--df-radius-sm)",
    color: "var(--df-fg-subtle)",
    fontSize: "var(--df-fs-md)",
    cursor: "pointer",
    border: "none",
    background: "transparent",
  };

  const agentNameStyle: CSSProperties = {
    fontSize: "var(--df-fs-md)",
    fontWeight: 600,
    color: "var(--df-fg)",
    fontFamily: "var(--df-font-mono)",
    letterSpacing: "var(--df-tracking-mono)",
  };

  const metaStyle: CSSProperties = {
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg-subtle)",
    marginTop: 2,
  };

  const hasSpans = (event.spans?.length ?? 0) > 0;
  const durationText =
    event.duration_ms != null ? `${event.duration_ms}ms` : "running…";

  return (
    <>
      <div style={overlayStyle} onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label={`Execution detail: ${event.agent}`}
        style={panelStyle}
      >
        {/* Header */}
        <div style={headerStyle}>
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--df-space-2)" }}>
              <span style={agentNameStyle}>{event.agent}</span>
              <StatusBadge status={event.status} />
            </div>
            <div style={metaStyle}>
              {durationText}
              {event.source_ids.length > 0 && ` · ${event.source_ids.join(", ")}`}
            </div>
          </div>
          <button
            style={closeBtnStyle}
            onClick={onClose}
            aria-label="Close detail panel"
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div style={contentStyle}>
          {/* Conversation context */}
          {event.conversation && (
            <section>
              <SectionLabel>Conversation Context</SectionLabel>
              <div style={conversationBoxStyle}>{event.conversation}</div>
            </section>
          )}

          {/* Execution timeline waterfall */}
          {hasSpans && (
            <section>
              <SectionLabel>Execution Timeline</SectionLabel>
              <TimelineWaterfall spans={event.spans!} eventStartedAt={event.started_at} />
            </section>
          )}

          {/* Full SQL */}
          <section>
            <SectionLabel>Statement</SectionLabel>
            <pre style={sqlPreStyle}>{event.statement}</pre>
          </section>

          {/* Tags */}
          {event.tags.length > 0 && (
            <section>
              <SectionLabel>Tags</SectionLabel>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--df-space-1)" }}>
                {event.tags.map((tag) => (
                  <span
                    key={tag}
                    style={{
                      padding: "2px var(--df-space-2)",
                      borderRadius: "var(--df-radius-pill)",
                      background: "var(--df-accent-soft)",
                      color: "var(--df-accent)",
                      fontSize: "var(--df-fs-xs)",
                      fontFamily: "var(--df-font-mono)",
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </section>
          )}
        </div>

        {/* Action bar */}
        <div style={actionBarStyle}>
          <button
            style={primaryBtnStyle}
            onClick={() => onOpenInQuery?.(event.statement)}
            disabled={!onOpenInQuery}
          >
            Open in Query Mode
          </button>
          <button style={secondaryBtnStyle} onClick={() => copy(event.statement)}>
            {copied ? "Copied ✓" : "Copy SQL"}
          </button>
        </div>
      </aside>
    </>
  );
}