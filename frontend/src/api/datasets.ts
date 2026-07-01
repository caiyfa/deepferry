import { api } from "./client";

// ── Types ────────────────────────────────────────────────

export type DatasetFormat = "parquet" | "json" | "arrow";
export type RefreshStrategy = "manual" | "hourly" | "daily" | "weekly";

export interface DatasetVersion {
  version: number; // v1, v2, v3...
  created_at: string; // ISO timestamp
  row_count: number;
  size_bytes: number;
  checksum?: string;
}

export interface Dataset {
  id: string;
  name: string;
  description?: string;
  source_ids: string[];
  sql: string; // the query that generates this dataset
  format: DatasetFormat;
  refresh_strategy: RefreshStrategy;
  versions: DatasetVersion[];
  latest_version: number;
  created_at: string;
  updated_at: string;
}

export interface CreateDatasetSpec {
  name: string;
  description?: string;
  source_ids: string[];
  sql: string;
  format: DatasetFormat;
  refresh_strategy: RefreshStrategy;
}

export interface DiffResult {
  additions: Record<string, unknown>[];
  deletions: Record<string, unknown>[];
  modifications: {
    key: Record<string, unknown>;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
  }[];
  summary: {
    added: number;
    deleted: number;
    modified: number;
    unchanged: number;
  };
}

// ── Mock data ────────────────────────────────────────────

const MOCK_DATASETS: Dataset[] = [
  {
    id: "ds-1",
    name: "Customer Summary",
    description: "Active customers with order totals",
    source_ids: ["mysql-main"],
    sql: "SELECT c.id, c.name, c.email, COUNT(o.id) as order_count, SUM(o.amount) as total_spent FROM customers c LEFT JOIN orders o ON c.id = o.customer_id WHERE c.status = 'active' GROUP BY c.id",
    format: "parquet",
    refresh_strategy: "daily",
    versions: [
      { version: 1, created_at: "2025-06-25T10:00:00Z", row_count: 142, size_bytes: 12400 },
      { version: 2, created_at: "2025-06-27T10:00:00Z", row_count: 145, size_bytes: 12800 },
      { version: 3, created_at: "2025-06-29T10:00:00Z", row_count: 148, size_bytes: 13200 },
    ],
    latest_version: 3,
    created_at: "2025-06-25T10:00:00Z",
    updated_at: "2025-06-29T10:00:00Z",
  },
  {
    id: "ds-2",
    name: "Revenue Report",
    description: "Monthly revenue by product category",
    source_ids: ["mysql-main", "finance-api"],
    sql: "SELECT category, SUM(amount) as revenue, COUNT(*) as sales FROM products JOIN orders ON products.id = orders.product_id GROUP BY category",
    format: "parquet",
    refresh_strategy: "weekly",
    versions: [
      { version: 1, created_at: "2025-06-20T08:00:00Z", row_count: 8, size_bytes: 2400 },
      { version: 2, created_at: "2025-06-27T08:00:00Z", row_count: 8, size_bytes: 2500 },
    ],
    latest_version: 2,
    created_at: "2025-06-20T08:00:00Z",
    updated_at: "2025-06-27T08:00:00Z",
  },
];

function generateMockDiff(v1: number, v2: number): DiffResult {
  const baseRows = Math.floor(v1 * 10 + 100);
  return {
    additions: Array.from({ length: Math.max(0, v2 - v1) * 3 }, (_, i) => ({
      id: baseRows + i + 1,
      name: `New Customer ${i + 1}`,
      email: `new${i + 1}@example.com`,
    })),
    deletions: v2 < v1 ? [{ id: 50, name: "Removed Customer", email: "removed@example.com" }] : [],
    modifications: [
      {
        key: { id: 42 },
        before: { name: "John Doe", total_spent: 1200 },
        after: { name: "John Doe", total_spent: 1450 },
      },
    ],
    summary: {
      added: Math.max(0, v2 - v1) * 3,
      deleted: v2 < v1 ? 1 : 0,
      modified: 1,
      unchanged: baseRows - 1,
    },
  };
}

// ── Public API ───────────────────────────────────────────

export const datasetsApi = {
  async list(): Promise<Dataset[]> {
    try {
      const res = await fetch(`${api.baseUrl}/api/datasets`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as Dataset[];
    } catch {
      return MOCK_DATASETS;
    }
  },

  async get(id: string): Promise<Dataset | null> {
    try {
      const res = await fetch(`${api.baseUrl}/api/datasets/${encodeURIComponent(id)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as Dataset;
    } catch {
      return MOCK_DATASETS.find((d) => d.id === id) ?? null;
    }
  },

  async create(spec: CreateDatasetSpec): Promise<Dataset> {
    try {
      const res = await fetch(`${api.baseUrl}/api/datasets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(spec),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as Dataset;
    } catch {
      // Mock: create new dataset
      const now = new Date().toISOString();
      return {
        id: `ds-${Date.now()}`,
        name: spec.name,
        description: spec.description,
        source_ids: spec.source_ids,
        sql: spec.sql,
        format: spec.format,
        refresh_strategy: spec.refresh_strategy,
        versions: [{ version: 1, created_at: now, row_count: 0, size_bytes: 0 }],
        latest_version: 1,
        created_at: now,
        updated_at: now,
      };
    }
  },

  async delete(id: string): Promise<void> {
    try {
      await fetch(`${api.baseUrl}/api/datasets/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch {
      void 0;
    }
  },

  async snapshot(id: string): Promise<DatasetVersion> {
    try {
      const res = await fetch(`${api.baseUrl}/api/datasets/${encodeURIComponent(id)}/snapshot`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as DatasetVersion;
    } catch {
      const now = new Date().toISOString();
      return {
        version: Math.floor(Math.random() * 10) + 4,
        created_at: now,
        row_count: Math.floor(Math.random() * 200) + 100,
        size_bytes: Math.floor(Math.random() * 20000) + 5000,
      };
    }
  },

  async diff(id: string, v1: number, v2: number): Promise<DiffResult> {
    try {
      const res = await fetch(
        `${api.baseUrl}/api/datasets/${encodeURIComponent(id)}/diff?v1=${v1}&v2=${v2}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as DiffResult;
    } catch {
      return generateMockDiff(v1, v2);
    }
  },

  /** Returns the export URL for download. */
  exportUrl(id: string, format: DatasetFormat, version: number): string {
    return `${api.baseUrl}/api/datasets/${encodeURIComponent(id)}/export?format=${format}&version=v${version}`;
  },
};
