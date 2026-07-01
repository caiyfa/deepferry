import { api } from "./client";
import type { ColumnMeta } from "./types";

// ── Types ────────────────────────────────────────────────

export interface ExploreResult {
  summary: string;
  sql: string;
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
  row_count: number;
  execution_time_ms: number;
  source_ids: string[];
}

export type ExploreStep =
  | "identifying_sources"
  | "fetching_schema"
  | "generating_sql"
  | "validating_sql"
  | "executing"
  | "done"
  | "error";

export interface ProgressEvent {
  step: ExploreStep;
  message: string;
  result?: ExploreResult;
  error?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  result?: ExploreResult;
}

// ── Mock data ────────────────────────────────────────────

const MOCK_SUGGESTIONS = [
  "What are the top 5 customers by total order amount?",
  "Show me orders from last month grouped by status",
  "Which products have the highest return rate?",
  "Compare revenue across all data sources",
];

const MOCK_RESULT: ExploreResult = {
  summary: "Found 3 products with the highest sales last quarter.",
  sql: "SELECT product_name, SUM(quantity) AS total_qty, SUM(amount) AS revenue\nFROM orders\nWHERE created_at >= '2025-01-01'\nGROUP BY product_name\nORDER BY revenue DESC\nLIMIT 3",
  columns: [
    { name: "product_name", type: "TEXT", nullable: false },
    { name: "total_qty", type: "INTEGER", nullable: false },
    { name: "revenue", type: "REAL", nullable: false },
  ],
  rows: [
    { product_name: "Premium Widget", total_qty: 142, revenue: 28400.0 },
    { product_name: "Smart Gadget", total_qty: 98, revenue: 19600.0 },
    { product_name: "Pro Device", total_qty: 76, revenue: 15200.0 },
  ],
  row_count: 3,
  execution_time_ms: 42.5,
  source_ids: [],
};

async function* mockAsk(
  question: string,
  _sourceIds: string[],
): AsyncGenerator<ProgressEvent> {
  const steps: Array<[ExploreStep, string]> = [
    ["identifying_sources", "Identifying relevant data sources…"],
    ["fetching_schema", "Fetching table schemas…"],
    ["generating_sql", `Translating "${question}" into SQL…`],
    ["validating_sql", "Validating SQL safety…"],
    ["executing", "Executing query…"],
  ];

  for (const [step, message] of steps) {
    await delay(400);
    yield { step, message };
  }

  await delay(300);
  yield {
    step: "done",
    message: MOCK_RESULT.summary,
    result: { ...MOCK_RESULT, source_ids: _sourceIds },
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Real SSE implementation ──────────────────────────────

async function* realAsk(
  question: string,
  sourceIds: string[],
  history?: ChatMessage[],
): AsyncGenerator<ProgressEvent> {
  const response = await fetch(`${api.baseUrl}/api/explore`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      question,
      source_ids: sourceIds,
      conversation_history: history?.map((m) => ({
        role: m.role,
        content: m.content,
      })),
    }),
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    const errMsg =
      errorBody?.detail?.message ??
      errorBody?.message ??
      `HTTP ${response.status}`;
    yield { step: "error", message: errMsg, error: errMsg };
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    yield {
      step: "error",
      message: "No response body",
      error: "No response body",
    };
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const dataLine = rawEvent
        .split("\n")
        .find((l) => l.startsWith("data:"));
      if (!dataLine) continue;

      const jsonStr = dataLine.slice(5).trim();
      try {
        const event = JSON.parse(jsonStr) as ProgressEvent;
        yield event;
      } catch (e) {
        void e;
      }
    }
  }
}

// ── Public API ───────────────────────────────────────────

export const exploreApi = {
  ask(
    question: string,
    sourceIds: string[],
    history?: ChatMessage[],
  ): AsyncGenerator<ProgressEvent> {
    const useMock =
      !api.baseUrl ||
      import.meta.env.VITE_EXPLORE_MOCK === "true";
    if (useMock) {
      return mockAsk(question, sourceIds);
    }
    return realAsk(question, sourceIds, history);
  },

  async getSuggestions(sourceIds: string[]): Promise<string[]> {
    try {
      const params = new URLSearchParams({
        source_ids: sourceIds.join(","),
      });
      const res = await fetch(
        `${api.baseUrl}/api/explore/suggestions?${params}`,
      );
      if (!res.ok) return MOCK_SUGGESTIONS;
      const data = (await res.json()) as { suggestions: string[] };
      return data.suggestions;
    } catch {
      return MOCK_SUGGESTIONS;
    }
  },
};
