import { useEffect, useState, type CSSProperties } from "react";
import {
  datasetsApi,
  type Dataset,
  type DatasetFormat,
  type DatasetVersion,
  type DiffResult,
} from "@/api/datasets";
import { DiffView } from "@/components/DiffView";
import { EmptyState } from "@/components/EmptyState";
import { ErrorBanner } from "@/components/ErrorBanner";

export interface DatasetDetailPageProps {
  datasetId: string;
  onBack?: () => void;
}

/** Structurally compatible with SidecarError (see @/api/client). */
interface AppError {
  name: string;
  message: string;
  code: string;
  status: number;
  suggestion?: string;
}

const EXPORT_FORMATS: DatasetFormat[] = ["parquet", "json", "arrow"];

const pageStyle: CSSProperties = {
  padding: "var(--df-space-4)",
  overflowY: "auto",
  height: "100%",
};

const headerStyle: CSSProperties = {
  marginBottom: "var(--df-space-4)",
};

const headerTopStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "var(--df-space-3)",
};

const backBtnStyle: CSSProperties = {
  background: "none",
  border: "none",
  color: "var(--df-accent)",
  cursor: "pointer",
  fontSize: "var(--df-fs-sm)",
  padding: 0,
  marginTop: "var(--df-space-1)",
  fontFamily: "var(--df-font-sans)",
};

const titleBlockStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
};

const titleStyle: CSSProperties = {
  fontSize: "var(--df-fs-lg)",
  fontWeight: 600,
  color: "var(--df-fg)",
  letterSpacing: "var(--df-tracking-tight)",
};

const descStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
  fontSize: "var(--df-fs-sm)",
  marginTop: "var(--df-space-1)",
};

const metaStyle: CSSProperties = {
  display: "flex",
  gap: "var(--df-space-3)",
  marginTop: "var(--df-space-2)",
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  flexWrap: "wrap",
  alignItems: "center",
};

const badgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: "0 var(--df-space-2)",
  border: "1px solid var(--df-border-strong)",
  borderRadius: "var(--df-radius-pill)",
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-muted)",
  background: "var(--df-bg-elevated)",
  fontFamily: "var(--df-font-mono)",
};

const metaDotStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
};

const deleteBtnStyle: CSSProperties = {
  background: "transparent",
  border: "1px solid var(--df-red)",
  color: "var(--df-red)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  padding: "var(--df-space-1) var(--df-space-3)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-sans)",
  flexShrink: 0,
  transition: "background var(--df-dur-fast) var(--df-ease)",
};

const deleteConfirmStyle: CSSProperties = {
  ...deleteBtnStyle,
  background: "var(--df-red)",
  color: "var(--df-bg-deep)",
  borderColor: "var(--df-red)",
  fontWeight: 600,
};

const cancelBtnStyle: CSSProperties = {
  background: "none",
  border: "1px solid var(--df-border)",
  color: "var(--df-fg-subtle)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  padding: "var(--df-space-1) var(--df-space-3)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-sans)",
  flexShrink: 0,
};

const sqlToggleStyle: CSSProperties = {
  background: "none",
  border: "1px solid var(--df-border)",
  color: "var(--df-fg-muted)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  padding: "var(--df-space-1) var(--df-space-3)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-mono)",
  display: "inline-flex",
  alignItems: "center",
  gap: "var(--df-space-1)",
};

const sqlPreviewStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
  fontSize: "var(--df-fs-xs)",
  fontFamily: "var(--df-font-mono)",
  marginLeft: "var(--df-space-2)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const sqlBlockStyle: CSSProperties = {
  marginTop: "var(--df-space-2)",
  padding: "var(--df-space-3)",
  background: "var(--df-bg-sunken)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  color: "var(--df-fg)",
  fontFamily: "var(--df-font-mono)",
  fontSize: "var(--df-fs-xs)",
  lineHeight: "var(--df-lh-base)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  overflowX: "auto",
};

// ── Section headers ──────────────────────────────────────

const sectionHeaderStyle: CSSProperties = {
  fontSize: "var(--df-fs-sm)",
  fontWeight: 600,
  color: "var(--df-fg)",
  margin: "var(--df-space-4) 0 var(--df-space-2)",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
};

const primaryBtnStyle: CSSProperties = {
  background: "var(--df-accent-soft)",
  border: "1px solid var(--df-accent-line)",
  color: "var(--df-accent)",
  cursor: "pointer",
  fontSize: "var(--df-fs-xs)",
  padding: "var(--df-space-1) var(--df-space-3)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-sans)",
  transition: "background var(--df-dur-fast) var(--df-ease)",
};

const primaryBtnDisabledStyle: CSSProperties = {
  ...primaryBtnStyle,
  opacity: 0.5,
  cursor: "not-allowed",
};

const timelineStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--df-space-2)",
};

const versionRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--df-space-3)",
  padding: "var(--df-space-2) var(--df-space-3)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  background: "var(--df-bg-elevated)",
};

const versionDotLatestStyle: CSSProperties = {
  color: "var(--df-green)",
  fontSize: "var(--df-fs-md)",
  lineHeight: 1,
};

const versionDotStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
  fontSize: "var(--df-fs-md)",
  lineHeight: 1,
};

const versionNumStyle: CSSProperties = {
  fontFamily: "var(--df-font-mono)",
  fontWeight: 600,
  color: "var(--df-fg)",
  fontSize: "var(--df-fs-sm)",
  minWidth: "2.5rem",
};

const versionMetaStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
  fontSize: "var(--df-fs-xs)",
  fontFamily: "var(--df-font-mono)",
};

const versionMetaMutedStyle: CSSProperties = {
  ...versionMetaStyle,
  color: "var(--df-fg-muted)",
};

const exportSelectStyle: CSSProperties = {
  marginLeft: "auto",
  background: "var(--df-bg-sunken)",
  border: "1px solid var(--df-border)",
  color: "var(--df-fg-muted)",
  fontSize: "var(--df-fs-xs)",
  padding: "var(--df-space-1) var(--df-space-2)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-sans)",
  cursor: "pointer",
};

const diffControlsStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--df-space-2)",
  flexWrap: "wrap",
};

const versionSelectStyle: CSSProperties = {
  background: "var(--df-bg-sunken)",
  border: "1px solid var(--df-border)",
  color: "var(--df-fg)",
  fontSize: "var(--df-fs-sm)",
  padding: "var(--df-space-1) var(--df-space-2)",
  borderRadius: "var(--df-radius)",
  fontFamily: "var(--df-font-mono)",
  cursor: "pointer",
};

const vsStyle: CSSProperties = {
  color: "var(--df-fg-subtle)",
  fontSize: "var(--df-fs-xs)",
};

const diffWrapStyle: CSSProperties = {
  marginTop: "var(--df-space-3)",
  padding: "var(--df-space-3)",
  border: "1px solid var(--df-border)",
  borderRadius: "var(--df-radius)",
  background: "var(--df-bg-elevated)",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1048576).toFixed(1)}MB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface VersionRowProps {
  version: DatasetVersion;
  isLatest: boolean;
  onExport: (version: number, format: DatasetFormat) => void;
}

function VersionRow({ version, isLatest, onExport }: VersionRowProps) {
  return (
    <div style={versionRowStyle}>
      <span style={isLatest ? versionDotLatestStyle : versionDotStyle}>
        {isLatest ? "●" : "○"}
      </span>
      <span style={versionNumStyle}>v{version.version}</span>
      <span style={versionMetaMutedStyle}>{formatDate(version.created_at)}</span>
      <span style={versionMetaStyle}>{version.row_count} rows</span>
      <span style={versionMetaStyle}>{formatSize(version.size_bytes)}</span>
      {version.checksum ? (
        <span style={versionMetaMutedStyle} title={version.checksum}>
          {version.checksum.slice(0, 8)}
        </span>
      ) : null}
      <select
        style={exportSelectStyle}
        value=""
        onChange={(e) => {
          const fmt = e.target.value as DatasetFormat;
          if (fmt) onExport(version.version, fmt);
        }}
        aria-label={`Export version v${version.version}`}
      >
        <option value="">Export ▾</option>
        {EXPORT_FORMATS.map((f) => (
          <option key={f} value={f}>
            {f}
          </option>
        ))}
      </select>
    </div>
  );
}

export function DatasetDetailPage({ datasetId, onBack }: DatasetDetailPageProps) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [snapshotLoading, setSnapshotLoading] = useState<boolean>(false);
  const [showSql, setShowSql] = useState<boolean>(false);
  const [confirmDelete, setConfirmDelete] = useState<boolean>(false);
  const [diffV1, setDiffV1] = useState<number>(0);
  const [diffV2, setDiffV2] = useState<number>(0);
  const [diffResult, setDiffResult] = useState<DiffResult | null>(null);
  const [diffLoading, setDiffLoading] = useState<boolean>(false);
  const [error, setError] = useState<AppError | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDataset(null);
    datasetsApi
      .get(datasetId)
      .then((d) => {
        if (cancelled) return;
        setDataset(d);
        if (d && d.versions.length > 0) {
          const first = d.versions[0].version;
          const latest = d.latest_version;
          setDiffV1(first);
          setDiffV2(latest);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setError({
          name: "SidecarError",
          code: "DATASET_LOAD_FAILED",
          message: `Failed to load dataset "${datasetId}".`,
          status: 0,
          suggestion: "Check the dataset id and that the sidecar is reachable.",
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  async function handleSnapshot(): Promise<void> {
    setSnapshotLoading(true);
    setError(null);
    try {
      await datasetsApi.snapshot(datasetId);
      const fresh = await datasetsApi.get(datasetId);
      setDataset(fresh);
    } catch {
      setError({
        name: "SidecarError",
        code: "SNAPSHOT_FAILED",
        message: `Could not snapshot dataset "${datasetId}".`,
        status: 0,
        suggestion: "Ensure the source is reachable and try again.",
      });
    } finally {
      setSnapshotLoading(false);
    }
  }

  async function handleCompare(): Promise<void> {
    if (!diffV1 || !diffV2 || diffV1 === diffV2) return;
    setDiffLoading(true);
    setError(null);
    try {
      const result = await datasetsApi.diff(datasetId, diffV1, diffV2);
      setDiffResult(result);
    } catch {
      setError({
        name: "SidecarError",
        code: "DIFF_FAILED",
        message: `Could not diff v${diffV1} → v${diffV2}.`,
        status: 0,
        suggestion: "Try selecting different versions.",
      });
    } finally {
      setDiffLoading(false);
    }
  }

  function handleExport(version: number, format: DatasetFormat): void {
    const url = datasetsApi.exportUrl(datasetId, format, version);
    window.open(url, "_blank");
  }

  async function handleDelete(): Promise<void> {
    try {
      await datasetsApi.delete(datasetId);
      onBack?.();
    } catch {
      setError({
        name: "SidecarError",
        code: "DELETE_FAILED",
        message: `Could not delete dataset "${datasetId}".`,
        status: 0,
        suggestion: "Try again in a moment.",
      });
      setConfirmDelete(false);
    }
  }

  if (loading) {
    return (
      <div style={pageStyle}>
        <div className="df-skeleton">
          <div className="df-skeleton__line" style={{ width: "30%" }} />
          <div className="df-skeleton__line" style={{ width: "90%" }} />
          <div className="df-skeleton__line" style={{ width: "60%" }} />
          <div className="df-skeleton__line" style={{ width: "80%" }} />
          <div className="df-skeleton__line" style={{ width: "50%" }} />
        </div>
      </div>
    );
  }

  if (!dataset) {
    return (
      <div style={pageStyle}>
        {onBack ? (
          <button style={backBtnStyle} onClick={onBack}>
            ← Back
          </button>
        ) : null}
        <EmptyState
          glyph="≋"
          title="Dataset not found"
          sub={`No dataset with id "${datasetId}" exists.`}
        />
      </div>
    );
  }

  const versions = [...dataset.versions].sort((a, b) => b.version - a.version);
  const canCompare = diffV1 > 0 && diffV2 > 0 && diffV1 !== diffV2;

  return (
    <div style={pageStyle} className="df-fade-in">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <header style={headerStyle}>
        <div style={headerTopStyle}>
          {onBack ? (
            <button style={backBtnStyle} onClick={onBack}>
              ← Back
            </button>
          ) : null}
          <div style={titleBlockStyle}>
            <h1 style={titleStyle}>{dataset.name}</h1>
            {dataset.description ? (
              <div style={descStyle}>{dataset.description}</div>
            ) : null}
            <div style={metaStyle}>
              {dataset.source_ids.map((sid) => (
                <span key={sid} style={badgeStyle}>
                  {sid}
                </span>
              ))}
              <span style={metaDotStyle}>·</span>
              <span>Format: {dataset.format}</span>
              <span style={metaDotStyle}>·</span>
              <span>Refresh: {dataset.refresh_strategy}</span>
              <span style={metaDotStyle}>·</span>
              <span>Updated {formatDateTime(dataset.updated_at)}</span>
            </div>
          </div>
          {confirmDelete ? (
            <>
              <button
                style={deleteConfirmStyle}
                onClick={() => void handleDelete()}
              >
                Confirm delete?
              </button>
              <button style={cancelBtnStyle} onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button style={deleteBtnStyle} onClick={() => setConfirmDelete(true)}>
              Delete Dataset
            </button>
          )}
        </div>
      </header>

      <div>
        <button style={sqlToggleStyle} onClick={() => setShowSql((v) => !v)}>
          <span>{showSql ? "▾" : "▸"}</span>
          <span>SQL</span>
          {!showSql ? (
            <span style={sqlPreviewStyle}>
              {dataset.sql.length > 80
                ? `${dataset.sql.slice(0, 80)}…`
                : dataset.sql}
            </span>
          ) : null}
        </button>
        {showSql ? <pre style={sqlBlockStyle}>{dataset.sql}</pre> : null}
      </div>

      <div style={sectionHeaderStyle}>
        <span>Version Timeline</span>
        <button
          style={snapshotLoading ? primaryBtnDisabledStyle : primaryBtnStyle}
          onClick={() => void handleSnapshot()}
          disabled={snapshotLoading}
        >
          {snapshotLoading ? "Snapshotting…" : "Take Snapshot"}
        </button>
      </div>
      <div style={timelineStyle}>
        {versions.map((v) => (
          <VersionRow
            key={v.version}
            version={v}
            isLatest={v.version === dataset.latest_version}
            onExport={handleExport}
          />
        ))}
      </div>

      <div style={sectionHeaderStyle}>
        <span>Compare Versions</span>
      </div>
      <div style={diffControlsStyle}>
        <select
          style={versionSelectStyle}
          value={diffV1}
          onChange={(e) => setDiffV1(Number(e.target.value))}
          aria-label="Base version"
        >
          {dataset.versions.map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version}
            </option>
          ))}
        </select>
        <span style={vsStyle}>vs</span>
        <select
          style={versionSelectStyle}
          value={diffV2}
          onChange={(e) => setDiffV2(Number(e.target.value))}
          aria-label="Target version"
        >
          {dataset.versions.map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version}
            </option>
          ))}
        </select>
        <button
          style={canCompare && !diffLoading ? primaryBtnStyle : primaryBtnDisabledStyle}
          onClick={() => void handleCompare()}
          disabled={!canCompare || diffLoading}
        >
          {diffLoading ? "Comparing…" : "Compare"}
        </button>
      </div>
      <div style={diffWrapStyle}>
        <DiffView diff={diffResult} loading={diffLoading} />
      </div>
    </div>
  );
}
