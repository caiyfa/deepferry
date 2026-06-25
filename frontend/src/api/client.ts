import type {
  ApiError,
  Execution,
  HealthStatus,
  QueryHistoryItem,
  QueryRequestBody,
  QueryResult,
  Schema,
  SourceSummary,
} from "./types";

const SIDECAR_URL =
  import.meta.env.VITE_SIDECAR_URL ?? "http://localhost:8000";

export class SidecarError extends Error {
  readonly code: string;
  readonly suggestion?: string;
  readonly status: number;

  constructor(code: string, message: string, status: number, suggestion?: string) {
    super(message);
    this.name = "SidecarError";
    this.code = code;
    this.status = status;
    this.suggestion = suggestion;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${SIDECAR_URL}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (err) {
    throw new SidecarError(
      "SIDECAR_UNREACHABLE",
      `Cannot reach the deepferry sidecar at ${SIDECAR_URL}.`,
      0,
      "Start it with: uv run deepferry mcp-server --transport http --port 8000",
    );
  }

  if (response.ok) {
    return response.json() as Promise<T>;
  }

  let detail: ApiError | { detail?: unknown } | null = null;
  try {
    detail = (await response.json()) as ApiError | { detail?: unknown };
  } catch {
    detail = null;
  }

  const apiErr = detail && "code" in detail ? detail : null;
  const message =
    apiErr?.message ??
    (detail && "detail" in detail ? JSON.stringify(detail.detail) : response.statusText);
  throw new SidecarError(
    apiErr?.code ?? `HTTP_${response.status}`,
    message,
    response.status,
    apiErr?.suggestion,
  );
}

export const api = {
  baseUrl: SIDECAR_URL,

  health(): Promise<{ status: string }> {
    return request<{ status: string }>("/health");
  },

  listSources(): Promise<SourceSummary[]> {
    return request<SourceSummary[]>("/api/config/sources");
  },

  testSource(sourceId: string): Promise<HealthStatus> {
    return request<HealthStatus>(
      `/api/config/sources/${encodeURIComponent(sourceId)}/test`,
      { method: "POST" },
    );
  },

  executeQuery(body: QueryRequestBody): Promise<QueryResult> {
    return request<QueryResult>("/api/query", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getSchema(sourceId: string): Promise<Schema> {
    return request<Schema>(`/api/schema/${encodeURIComponent(sourceId)}`);
  },

  getHistory(limit = 50, offset = 0): Promise<QueryHistoryItem[]> {
    return request<QueryHistoryItem[]>(
      `/api/history?limit=${limit}&offset=${offset}`,
    );
  },

  listExecutions(limit = 50, offset = 0): Promise<Execution[]> {
    return request<Execution[]>(
      `/api/executions?limit=${limit}&offset=${offset}`,
    );
  },

  getExecution(executionId: number): Promise<Execution> {
    return request<Execution>(`/api/executions/${executionId}`);
  },
};
