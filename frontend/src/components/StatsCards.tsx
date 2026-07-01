import type { CSSProperties } from "react";
import type { AgentStats } from "@/api/agents";

export interface StatsCardsProps {
  stats: AgentStats | null;
  loading?: boolean;
}

interface CardDef {
  label: string;
  icon: string;
  color: string;
  value: string;
}

function buildCards(stats: AgentStats | null): CardDef[] {
  if (stats === null) {
    return [
      { label: "Active Agents", icon: "◉", color: "var(--df-green)", value: "—" },
      { label: "Today's Queries", icon: "▦", color: "var(--df-accent)", value: "—" },
      { label: "Avg Latency", icon: "⚡", color: "var(--df-yellow)", value: "—" },
      { label: "Error Rate", icon: "⚠", color: "var(--df-red)", value: "—" },
    ];
  }
  return [
    {
      label: "Active Agents",
      icon: "◉",
      color: "var(--df-green)",
      value: String(stats.active_agents),
    },
    {
      label: "Today's Queries",
      icon: "▦",
      color: "var(--df-accent)",
      value: String(stats.today_queries),
    },
    {
      label: "Avg Latency",
      icon: "⚡",
      color: "var(--df-yellow)",
      value: stats.avg_latency != null ? `${stats.avg_latency.toFixed(0)}ms` : "—",
    },
    {
      label: "Error Rate",
      icon: "⚠",
      color: "var(--df-red)",
      value: `${(stats.error_rate * 100).toFixed(1)}%`,
    },
  ];
}

const containerStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--df-space-3)",
  padding: "var(--df-space-3) var(--df-space-4)",
};

function cardStyle(alertPulse: boolean): CSSProperties {
  return {
    flex: "1 1 140px",
    minWidth: "140px",
    border: alertPulse
      ? "1px solid var(--df-red)"
      : "1px solid var(--df-border)",
    borderRadius: "var(--df-radius-md)",
    padding: "var(--df-space-3) var(--df-space-4)",
    background: "var(--df-bg-elevated)",
    animation: alertPulse ? "df-pulse-border 1.6s infinite ease-in-out" : undefined,
  };
}

const iconStyle = (color: string): CSSProperties => ({
  fontSize: "var(--df-fs-lg)",
  color,
  lineHeight: 1,
});

const labelStyle: CSSProperties = {
  fontSize: "var(--df-fs-xs)",
  color: "var(--df-fg-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginTop: "var(--df-space-1)",
};

function valueStyle(color: string, muted: boolean): CSSProperties {
  return {
    fontSize: "var(--df-fs-xl)",
    fontWeight: 600,
    fontFamily: "var(--df-font-mono)",
    color: muted ? "var(--df-fg-subtle)" : color,
    marginTop: "var(--df-space-1)",
  };
}

export function StatsCards({ stats, loading = false }: StatsCardsProps) {
  const cards = buildCards(stats);
  const muted = stats === null || loading;
  const errorPulse = stats !== null && stats.error_rate > 0.05;

  return (
    <div style={containerStyle}>
      {cards.map((card, idx) => {
        const isErrorCard = idx === 3;
        const pulse = isErrorCard && errorPulse;
        return (
          <div
            key={card.label}
            style={cardStyle(pulse)}
            className={muted ? "df-skeleton" : undefined}
          >
            <div style={iconStyle(card.color)}>{card.icon}</div>
            <div style={labelStyle}>{card.label}</div>
            <div style={valueStyle(card.color, muted)}>{card.value}</div>
          </div>
        );
      })}
      <style>{`
        @keyframes df-pulse-border {
          0%, 100% { border-color: var(--df-red); box-shadow: 0 0 0 0 rgba(243, 139, 168, 0); }
          50% { border-color: var(--df-red); box-shadow: 0 0 0 2px rgba(243, 139, 168, 0.25); }
        }
        @media (max-width: 767px) {
          .df-statscards-row { flex-wrap: wrap; }
        }
      `}</style>
    </div>
  );
}