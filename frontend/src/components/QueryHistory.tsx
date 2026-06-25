import { Link } from "react-router-dom";
import type { QueryHistoryItem } from "@/api/types";
import { EmptyState } from "./EmptyState";

interface QueryHistoryProps {
  items: QueryHistoryItem[];
  loading: boolean;
}

function statusBadge(status: string): string {
  if (status === "ok" || status === "success") return "df-badge--ok";
  if (status === "error" || status === "failed") return "df-badge--err";
  return "df-badge--warn";
}

function formatTime(iso: string): string {
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function truncate(sql: string, max = 80): string {
  const one = sql.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

export function QueryHistory({ items, loading }: QueryHistoryProps) {
  if (loading && items.length === 0) {
    return (
      <div className="df-skeleton">
        <div className="df-skeleton__line" style={{ width: "40%" }} />
        <div className="df-skeleton__line" style={{ width: "92%" }} />
        <div className="df-skeleton__line" style={{ width: "76%" }} />
        <div className="df-skeleton__line" style={{ width: "88%" }} />
      </div>
    );
  }

  if (!loading && items.length === 0) {
    return (
      <EmptyState
        glyph="∅"
        title="No query history yet"
        sub="Executed queries will appear here with timing, row counts, and a link to their execution trace."
      />
    );
  }

  return (
    <div className="df-history df-fade-in">
      <div className="df-history__row df-history__head">
        <span>#</span>
        <span>source</span>
        <span>query</span>
        <span>rows</span>
        <span>status</span>
        <span>when</span>
      </div>
      {items.map((item) => (
        <Link key={item.id} className="df-history__row" to={`/history/${item.id}`}>
          <span className="df-mono df-subtle">{item.id}</span>
          <span className="df-mono">{item.source_id}</span>
          <span className="df-history__sql" title={item.query_text}>
            {truncate(item.query_text)}
          </span>
          <span className="df-mono">{item.row_count.toLocaleString()}</span>
          <span>
            <span className={`df-badge ${statusBadge(item.status)}`}>{item.status}</span>
          </span>
          <span className="df-history__time">{formatTime(item.created_at)}</span>
        </Link>
      ))}
    </div>
  );
}
