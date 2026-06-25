import { useMemo, useState } from "react";
import type { Span, SpanKind } from "@/api/types";

interface SpanTreeProps {
  spans: Span[];
}

interface SpanNode {
  span: Span;
  depth: number;
  children: SpanNode[];
}

const SENSITIVE_KEY = /password|secret|token|api_key|apikey|credential/i;

function redact(key: string, value: string | number | boolean): string {
  if (SENSITIVE_KEY.test(key)) return "••••••••";
  return String(value);
}

function buildTree(spans: Span[]): SpanNode[] {
  const byId = new Map<number, Span>();
  spans.forEach((s) => byId.set(s.id, s));

  const childrenOf = new Map<number | null, Span[]>();
  spans.forEach((s) => {
    const parent = s.parent_span_id;
    const list = childrenOf.get(parent) ?? [];
    list.push(s);
    childrenOf.set(parent, list);
  });

  const sortKey = (s: Span) => s.started_at;
  for (const list of childrenOf.values()) {
    list.sort((a, b) => sortKey(a) - sortKey(b));
  }

  const build = (parent: Span | null, depth: number): SpanNode[] => {
    const parentId = parent?.id ?? null;
    const kids = childrenOf.get(parentId) ?? [];
    return kids.map((span) => ({
      span,
      depth,
      children: build(span, depth + 1),
    }));
  };

  return build(null, 0);
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function spanDuration(span: Span): number | null {
  if (span.finished_at == null) return null;
  return span.finished_at - span.started_at;
}

function statusClass(span: Span): string {
  if (span.span_kind === "auth_retry") return "df-badge--retry";
  return span.status === "ok" ? "df-badge--ok" : "df-badge--err";
}

function statusLabel(span: Span): string {
  if (span.span_kind === "auth_retry") return "retry";
  return span.status;
}

const KIND_GLYPH: Record<SpanKind, string> = {
  orchestration: "⌥",
  sql_exec: "▸",
  http_request: "↯",
  auth: "⚿",
  auth_retry: "↻",
  materialize: "▤",
  join: "⋈",
  discover: "◎",
};

export function SpanTree({ spans }: SpanTreeProps) {
  const tree = useMemo(() => buildTree(spans), [spans]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderNode = (node: SpanNode): React.ReactNode => {
    const { span, depth, children } = node;
    const isOpen = expanded.has(span.id);
    const dur = spanDuration(span);
    const attrs = Object.entries(span.attributes);

    return (
      <div key={span.id}>
        <div
          className="df-span"
          style={{ paddingLeft: `calc(var(--df-space-4) + ${depth * 20}px)` }}
          onClick={() => toggle(span.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggle(span.id);
            }
          }}
        >
          <span className={`df-span__guide${depth === 0 ? " is-root" : ""}`} />
          <span className="df-span__name">
            <span className="df-subtle" style={{ marginRight: "0.5ch" }}>
              {KIND_GLYPH[span.span_kind]}
            </span>
            {span.span_name}
          </span>
          <span className="df-span__kind">{span.span_kind}</span>
          <span className="df-span__dur">{formatDuration(dur)}</span>
          <span>
            <span className={`df-badge ${statusClass(span)}`}>
              {statusLabel(span)}
            </span>
          </span>
        </div>

        {isOpen && attrs.length > 0 ? (
          <dl
            className="df-span__attrs"
            style={{ marginLeft: `calc(var(--df-space-4) + ${depth * 20 + 32}px)` }}
          >
            {attrs.map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{redact(k, v)}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {isOpen && attrs.length === 0 ? (
          <div
            className="df-span__attrs df-subtle"
            style={{ marginLeft: `calc(var(--df-space-4) + ${depth * 20 + 32}px)` }}
          >
            no attributes captured
          </div>
        ) : null}

        {children.map((c) => renderNode(c))}
      </div>
    );
  };

  return <div className="df-spantree">{tree.map((n) => renderNode(n))}</div>;
}
