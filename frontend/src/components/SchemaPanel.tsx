import { useState, useEffect, useMemo, type CSSProperties } from "react";
import { api, SidecarError } from "@/api/client";
import type { ResourceMeta, ColumnMeta } from "@/api/types";

interface SchemaPanelProps {
  sourceIds: string[];
  onInsert: (text: string) => void;
}

type SchemaMap = Record<string, ResourceMeta[] | { error: string }>;

const ARROW_COLLAPSED = "▸";
const ARROW_EXPANDED = "▾";

export function SchemaPanel({ sourceIds, onInsert }: SchemaPanelProps) {
  const [schemas, setSchemas] = useState<SchemaMap>({});
  const [loading, setLoading] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<string>>(
    () => new Set(sourceIds),
  );
  const [expandedTables, setExpandedTables] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (sourceIds.length === 0) {
      setSchemas({});
      return;
    }
    setLoading(true);
    setExpandedSources(new Set(sourceIds));
    setExpandedTables(new Set());

    (async () => {
      const entries = await Promise.all(
        sourceIds.map(async (id) => {
          try {
            const schema = await api.getSchema(id);
            return [id, schema.resources] as const;
          } catch (err) {
            const message =
              err instanceof SidecarError ? err.message : "Failed to load schema";
            return [id, { error: message }] as const;
          }
        }),
      );
      if (cancelled) return;
      const next: SchemaMap = {};
      for (const [id, value] of entries) {
        next[id] = value;
      }
      setSchemas(next);
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [sourceIds]);

  const normalizedSearch = search.trim().toLowerCase();
  const isSearching = normalizedSearch.length > 0;

  const filtered = useMemo(() => {
    if (!isSearching) return null;
    const result: Record<
      string,
      { resource: ResourceMeta; columns: ColumnMeta[] }[]
    > = {};
    for (const sourceId of sourceIds) {
      const entry = schemas[sourceId];
      if (!entry || "error" in entry) continue;
      const matched: { resource: ResourceMeta; columns: ColumnMeta[] }[] = [];
      for (const resource of entry) {
        const tableMatches = resource.name.toLowerCase().includes(normalizedSearch);
        const matchedColumns = resource.columns.filter(
          (c) =>
            c.name.toLowerCase().includes(normalizedSearch) ||
            c.type.toLowerCase().includes(normalizedSearch),
        );
        if (tableMatches || matchedColumns.length > 0) {
          matched.push({
            resource,
            columns: tableMatches ? resource.columns : matchedColumns,
          });
        }
      }
      if (matched.length > 0) result[sourceId] = matched;
    }
    return result;
  }, [schemas, sourceIds, isSearching, normalizedSearch]);

  const effectiveExpandedSources = isSearching
    ? new Set(sourceIds)
    : expandedSources;
  const effectiveExpandedTables = isSearching
    ? new Set(
        Object.values(schemas).flatMap((entry) =>
          "error" in entry || !entry
            ? []
            : entry.map((r) => `${r.name}`),
        ),
      )
    : expandedTables;

  const toggleSource = (id: string) => {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleTable = (name: string) => {
    setExpandedTables((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const containerStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    minHeight: 0,
  };

  const searchStyle: CSSProperties = {
    margin: "var(--df-space-2) var(--df-space-3)",
    padding: "var(--df-space-2)",
    background: "var(--df-bg)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius)",
    color: "var(--df-fg)",
    fontSize: "var(--df-fs-sm)",
    outline: "none",
  };

  const treeStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    padding: "0 var(--df-space-2) var(--df-space-3)",
  };

  const sourceHeaderStyle = (expanded: boolean): CSSProperties => ({
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg-subtle)",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    padding: "var(--df-space-2) var(--df-space-2)",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    gap: "var(--df-space-1)",
    userSelect: "none",
    opacity: expanded ? 1 : 0.7,
  });

  const tableRowStyle: CSSProperties = {
    padding: "var(--df-space-1) var(--df-space-3)",
    cursor: "pointer",
    fontSize: "var(--df-fs-sm)",
    color: "var(--df-fg)",
    display: "flex",
    alignItems: "center",
    gap: "var(--df-space-1)",
    borderRadius: "var(--df-radius-xs)",
    transition: "background var(--df-dur-fast) var(--df-ease)",
  };

  const columnRowStyle: CSSProperties = {
    padding: "var(--df-space-1) var(--df-space-5)",
    cursor: "pointer",
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg-subtle)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "var(--df-space-2)",
    borderRadius: "var(--df-radius-xs)",
    transition: "color var(--df-dur-fast) var(--df-ease)",
  };

  const columnTypeStyle: CSSProperties = {
    fontFamily: "var(--df-font-mono)",
    fontSize: "var(--df-fs-xs)",
    color: "var(--df-fg-subtle)",
    opacity: 0.8,
  };

  const bannerStyle: CSSProperties = {
    margin: "var(--df-space-2) var(--df-space-3) 0",
    padding: "var(--df-space-1) var(--df-space-3)",
    background: "var(--df-accent-soft)",
    border: "1px solid var(--df-accent-line)",
    borderRadius: "var(--df-radius)",
    color: "var(--df-accent)",
    fontSize: "var(--df-fs-xs)",
  };

  const spinnerStyle: CSSProperties = {
    padding: "var(--df-space-3)",
    color: "var(--df-fg-subtle)",
    fontSize: "var(--df-fs-sm)",
    textAlign: "center",
  };

  const errorStyle: CSSProperties = {
    padding: "var(--df-space-1) var(--df-space-3)",
    color: "var(--df-danger)",
    fontSize: "var(--df-fs-xs)",
  };

  const emptyStyle: CSSProperties = {
    padding: "var(--df-space-4) var(--df-space-3)",
    color: "var(--df-fg-subtle)",
    fontSize: "var(--df-fs-sm)",
    textAlign: "center",
  };

  const renderColumn = (
    sourceId: string,
    resource: ResourceMeta,
    column: ColumnMeta,
  ) => {
    const key = `${sourceId}:${resource.name}:${column.name}`;
    return (
      <div
        key={key}
        style={columnRowStyle}
        onClick={() => onInsert(`${resource.name}.${column.name}`)}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--df-accent)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--df-fg-subtle)";
        }}
        title={`Insert ${resource.name}.${column.name}`}
      >
        <span>{column.name}</span>
        <span style={columnTypeStyle}>
          {column.type}
          {column.nullable ? (
            <span style={{ color: "var(--df-yellow)", marginLeft: "0.15em" }}>
              ?
            </span>
          ) : null}
        </span>
      </div>
    );
  };

  const renderResource = (
    sourceId: string,
    resource: ResourceMeta,
    columns: ColumnMeta[],
  ) => {
    const expanded = effectiveExpandedTables.has(resource.name);
    return (
      <div key={`${sourceId}:${resource.name}`}>
        <div
          style={tableRowStyle}
          onClick={() => {
            if (!isSearching) toggleTable(resource.name);
            onInsert(resource.name);
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--df-bg-elevated)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
          title={`Insert ${resource.name}`}
        >
          <span style={{ color: "var(--df-fg-subtle)", width: "1em" }}>
            {expanded ? ARROW_EXPANDED : ARROW_COLLAPSED}
          </span>
          <span>{resource.name}</span>
          <span style={columnTypeStyle}>{resource.columns.length} cols</span>
        </div>
        {expanded
          ? columns.map((c) => renderColumn(sourceId, resource, c))
          : null}
      </div>
    );
  };

  const renderSource = (sourceId: string) => {
    const entry = schemas[sourceId];
    const expanded = effectiveExpandedSources.has(sourceId);
    const isError = entry !== undefined && "error" in entry;
    const resources = entry && !isError ? (entry as ResourceMeta[]) : [];

    return (
      <div key={sourceId}>
        <div
          style={sourceHeaderStyle(expanded)}
          onClick={() => toggleSource(sourceId)}
        >
          <span style={{ width: "1em" }}>
            {expanded ? ARROW_EXPANDED : ARROW_COLLAPSED}
          </span>
          <span>{sourceId}</span>
        </div>
        {expanded && isError ? (
          <div style={errorStyle}>
            ⚠ {((entry as { error: string }).error)}
          </div>
        ) : null}
        {expanded && !isError
          ? resources.length === 0
            ? (
              <div style={emptyStyle}>No resources</div>
            )
            : resources.map((r) =>
              renderResource(sourceId, r, r.columns),
            )
          : null}
      </div>
    );
  };

  const showBanner = sourceIds.length >= 2;

  return (
    <div style={containerStyle}>
      {showBanner ? (
        <div style={bannerStyle}>
          {sourceIds.length} sources selected — cross-source join hints available
        </div>
      ) : null}
      <input
        type="text"
        style={searchStyle}
        placeholder="Search tables and columns…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Search schema"
      />
      <div style={treeStyle}>
        {loading ? <div style={spinnerStyle}>Loading schema…</div> : null}
        {!loading && sourceIds.length === 0 ? (
          <div style={emptyStyle}>No data source selected</div>
        ) : null}
        {!loading && sourceIds.length > 0 ? (
          isSearching && filtered ? (
            Object.keys(filtered).length === 0 ? (
              <div style={emptyStyle}>No matches for "{search}"</div>
            ) : (
              Object.entries(filtered).map(([sourceId, resources]) => (
                <div key={sourceId}>
                  <div style={sourceHeaderStyle(true)}>
                    <span style={{ width: "1em" }}>{ARROW_EXPANDED}</span>
                    <span>{sourceId}</span>
                  </div>
                  {resources.map(({ resource, columns }) =>
                    renderResource(sourceId, resource, columns),
                  )}
                </div>
              ))
            )
          ) : (
            sourceIds.map(renderSource)
          )
        ) : null}
      </div>
    </div>
  );
}