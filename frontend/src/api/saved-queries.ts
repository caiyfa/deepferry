/**
 * Saved queries REST API client with mock fallback.
 *
 * The deepferry sidecar may not yet expose `/api/saved-queries`; when the
 * request fails (network error or non-OK status) we fall back to an
 * in-memory mock dataset so the UI remains functional during development.
 */
import { api } from "./client";

export interface SavedQuery {
  id: string;
  name: string;
  description?: string;
  statement: string;
  source_ids: string[];
  parameters?: string[]; // extracted {{param}} names
  created_at: string;
  updated_at: string;
}

export interface CreateSavedQuery {
  name: string;
  description?: string;
  statement: string;
  source_ids: string[];
}

/**
 * Extract `{{param}}` placeholders from a SQL statement.
 * Returns a de-duplicated list of parameter names in order of first appearance.
 */
export function extractParameters(sql: string): string[] {
  const matches = sql.match(/\{\{(\w+)\}\}/g);
  if (!matches) return [];
  return [...new Set(matches.map((m) => m.slice(2, -2)))];
}

/**
 * Replace `{{param}}` placeholders with actual values.
 *
 * String values are single-quoted with internal quotes doubled to keep the
 * resulting SQL syntactically valid. Missing values leave the placeholder
 * intact so the caller can detect unresolved parameters.
 */
export function applyParameters(
  sql: string,
  params: Record<string, string>,
): string {
  return sql.replace(/\{\{(\w+)\}\}/g, (_, name: string) => {
    const val = params[name];
    // Quote string values to prevent SQL injection in naive replacement
    return val !== undefined ? `'${val.replace(/'/g, "''")}'` : `{{${name}}}`;
  });
}

const MOCK_SAVED: SavedQuery[] = [
  {
    id: "sq-1",
    name: "Active customers",
    description: "All active customers with contact info",
    statement:
      "SELECT id, name, email, status FROM customers WHERE status = 'active' ORDER BY name",
    source_ids: ["mysql-main"],
    parameters: [],
    created_at: "2025-06-20T10:00:00Z",
    updated_at: "2025-06-20T10:00:00Z",
  },
  {
    id: "sq-2",
    name: "Orders by status",
    description: "Order count grouped by status",
    statement:
      "SELECT status, COUNT(*) as count FROM orders WHERE created_at >= '{{date}}' GROUP BY status",
    source_ids: ["mysql-main"],
    parameters: ["date"],
    created_at: "2025-06-21T14:00:00Z",
    updated_at: "2025-06-21T14:00:00Z",
  },
];

const BASE_URL = api.baseUrl;

export const savedQueriesApi = {
  async list(): Promise<SavedQuery[]> {
    try {
      const res = await fetch(`${BASE_URL}/api/saved-queries`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as SavedQuery[];
    } catch {
      return MOCK_SAVED;
    }
  },

  async create(query: CreateSavedQuery): Promise<SavedQuery> {
    try {
      const res = await fetch(`${BASE_URL}/api/saved-queries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(query),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as SavedQuery;
    } catch {
      // Mock: return a new query object
      const now = new Date().toISOString();
      return {
        id: `sq-${Date.now()}`,
        name: query.name,
        description: query.description,
        statement: query.statement,
        source_ids: query.source_ids,
        parameters: extractParameters(query.statement),
        created_at: now,
        updated_at: now,
      };
    }
  },

  async delete(id: string): Promise<void> {
    try {
      await fetch(`${BASE_URL}/api/saved-queries/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
    } catch {
      void 0;
    }
  },
};