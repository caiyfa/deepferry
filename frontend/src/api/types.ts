/**
 * TypeScript mirrors of the deepferry FastAPI sidecar Pydantic models.
 *
 * Field names are snake_case to match the backend's default JSON serialization
 * (Pydantic v2 emits field names verbatim).  Keep these in lock-step with
 * `src/deepferry/core/models.py` and `src/deepferry/core/trace.py`.
 */

export type SourceHealth = "healthy" | "unhealthy" | "unknown";

export interface SourceSummary {
  id: string;
  name: string;
  type: string;
  health: SourceHealth;
}

export interface HealthStatus {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

export interface ColumnMeta {
  name: string;
  type: string;
  nullable: boolean;
}

export interface ResourceMeta {
  name: string;
  columns: ColumnMeta[];
}

export interface Schema {
  resources: ResourceMeta[];
}

export interface QueryRequestBody {
  source_id: string;
  statement: string;
  params?: Record<string, unknown>;
  timeout?: number;
  max_rows?: number;
}

export interface QueryResult {
  columns: ColumnMeta[];
  rows: Record<string, unknown>[];
  row_count: number;
  execution_time_ms: number;
  source_id: string;
}

export interface QueryHistoryItem {
  id: number;
  source_id: string;
  query_text: string;
  row_count: number;
  status: string;
  execution_time_ms: number;
  created_at: string;
}

export type SpanKind =
  | "orchestration"
  | "sql_exec"
  | "http_request"
  | "auth"
  | "auth_retry"
  | "materialize"
  | "join"
  | "discover";

export type SpanStatus = "ok" | "error";

export interface Span {
  id: number;
  execution_id: number;
  parent_span_id: number | null;
  span_kind: SpanKind;
  span_name: string;
  source_id: string;
  started_at: number;
  finished_at: number | null;
  status: SpanStatus;
  attributes: Record<string, string | number | boolean>;
}

export interface Execution {
  id: number;
  root_query_id: number | null;
  source_id: string;
  scenario_id: string | null;
  session_id: string | null;
  started_at: number;
  finished_at: number | null;
  status: SpanStatus;
  spans: Span[];
}

export interface ApiError {
  code: string;
  message: string;
  suggestion?: string;
}
