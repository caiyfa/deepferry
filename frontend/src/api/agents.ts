// ── Sidecar URL (mirrored from client.ts, kept local to avoid mutating existing files) ──

const SIDECAR_URL =
  import.meta.env.VITE_SIDECAR_URL ?? "http://localhost:8000";

export { SIDECAR_URL };

// ── Types ────────────────────────────────────────────────

export interface AgentStats {
  active_agents: number;
  today_queries: number;
  avg_latency: number | null;
  error_rate: number;
}

export type FeedEventStatus = "running" | "ok" | "error";

export interface FeedEvent {
  id: string;
  agent: string;              // agent name/identifier
  statement: string;          // SQL or HTTP description
  status: FeedEventStatus;
  source_ids: string[];       // which data sources were queried
  started_at: number;         // unix timestamp ms
  duration_ms: number | null; // null while running
  conversation?: string;      // brief conversation context
  spans?: FeedSpan[];         // execution timeline spans
  tags: string[];             // e.g. ["cross-source", "auth-retry"]
}

export interface FeedSpan {
  id: string;
  kind: string;               // "sql_exec", "http_request", "auth", etc.
  name: string;
  started_at: number;
  finished_at: number | null;
  status: "ok" | "error";
}

export interface FilterState {
  agent: string | "all";
  status: FeedEventStatus | "all";
  source: string | "all";
}

// ── Mock data ────────────────────────────────────────────

const MOCK_AGENTS = ["claude-code", "cursor-dev", "copilot-agent"];
const MOCK_SOURCES = ["mysql-main", "finance-api"];
const MOCK_STATEMENTS = [
  "SELECT * FROM customers WHERE status = 'active'",
  "SELECT COUNT(*) FROM orders GROUP BY status",
  "GET /api/v1/products?category=electronics",
  "SELECT c.name, SUM(o.amount) FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.id",
  "SELECT * FROM transactions WHERE created_at >= '2025-01-01'",
];

let mockEventCounter = 0;

export function generateMockFeedEvent(): FeedEvent {
  const id = `mock-${++mockEventCounter}`;
  const agent = MOCK_AGENTS[Math.floor(Math.random() * MOCK_AGENTS.length)] ?? "agent";
  const statement = MOCK_STATEMENTS[Math.floor(Math.random() * MOCK_STATEMENTS.length)] ?? "SELECT 1";
  const status: FeedEventStatus = Math.random() > 0.85 ? "error" : Math.random() > 0.7 ? "running" : "ok";
  const startedAt = Date.now();
  const isRunning = status === "running";
  const duration = isRunning ? null : Math.floor(Math.random() * 800) + 20;

  return {
    id,
    agent,
    statement,
    status,
    source_ids: Math.random() > 0.6 ? MOCK_SOURCES : [MOCK_SOURCES[0] ?? "mysql-main"],
    started_at: startedAt,
    duration_ms: duration,
    conversation: `Agent asked: "${statement.slice(0, 60)}${statement.length > 60 ? "…" : ""}"`,
    spans: isRunning
      ? []
      : [
          {
            id: `${id}-span-1`,
            kind: "sql_exec",
            name: `Execute on ${MOCK_SOURCES[0] ?? "mysql"}`,
            started_at: startedAt,
            finished_at: startedAt + (duration ?? 0),
            status: status === "error" ? "error" : "ok",
          },
        ],
    tags: Math.random() > 0.7 ? ["cross-source"] : [],
  };
}

// ── Public API ───────────────────────────────────────────

export const agentsApi = {
  /** Fetch agent statistics. Returns mock if backend unavailable. */
  async getStats(): Promise<AgentStats> {
    try {
      const res = await fetch(`${SIDECAR_URL}/api/agents/stats`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as AgentStats;
    } catch {
      return {
        active_agents: Math.floor(Math.random() * 4),
        today_queries: Math.floor(Math.random() * 200) + 50,
        avg_latency: Math.floor(Math.random() * 300) + 50,
        error_rate: Math.random() * 0.15,
      };
    }
  },

  /** Fetch agent sessions (recent activity). Returns mock if unavailable. */
  async getSessions(limit: number = 50): Promise<FeedEvent[]> {
    try {
      const res = await fetch(`${SIDECAR_URL}/api/agents/sessions?limit=${limit}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as FeedEvent[];
    } catch {
      return Array.from({ length: 20 }, () => generateMockFeedEvent());
    }
  },
};
