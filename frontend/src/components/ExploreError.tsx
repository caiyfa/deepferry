interface ExploreErrorProps {
  message: string;
  onRetry?: () => void;
  onSwitchToQuery?: () => void;
  /** "error" (red, default) for LLM failures · "degraded" (yellow) for unavailable/degraded. */
  variant?: "error" | "degraded";
}

export function ExploreError({
  message,
  onRetry,
  onSwitchToQuery,
  variant = "error",
}: ExploreErrorProps) {
  const isError = variant === "error";
  const accentVar = isError ? "var(--df-red)" : "var(--df-yellow)";
  const softBg = isError
    ? "rgba(243, 139, 168, 0.10)" // --df-red #f38ba8 @ 10%
    : "rgba(249, 226, 175, 0.10)"; // --df-yellow #f9e2af @ 10%

  return (
    <div
      className="df-fade-in"
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--df-space-3)",
        border: `1px solid ${accentVar}`,
        borderRadius: "var(--df-radius-md)",
        background: softBg,
        padding: "var(--df-space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "var(--df-space-3)",
        }}
      >
        <span
          style={{
            color: accentVar,
            fontSize: "var(--df-fs-lg)",
            lineHeight: 1,
            flexShrink: 0,
          }}
        >
          ⚠
        </span>
        <span
          style={{
            color: "var(--df-fg)",
            fontSize: "var(--df-fs-sm)",
            lineHeight: "var(--df-lh-base)",
          }}
        >
          {message}
        </span>
      </div>

      {(onRetry || onSwitchToQuery) && (
        <div
          style={{
            display: "flex",
            gap: "var(--df-space-3)",
            alignItems: "center",
          }}
        >
          {onRetry && (
            <button
              className="df-btn df-btn--ghost df-btn--sm"
              onClick={onRetry}
            >
              Try another question
            </button>
          )}
          {onSwitchToQuery && (
            <button
              onClick={onSwitchToQuery}
              style={{
                background: "none",
                border: "none",
                color: "var(--df-accent)",
                fontSize: "var(--df-fs-sm)",
                fontFamily: "var(--df-font-mono)",
                letterSpacing: "var(--df-tracking-mono)",
                padding: 0,
                cursor: "pointer",
                textDecoration: "underline",
              }}
            >
              Go to Query mode →
            </button>
          )}
        </div>
      )}
    </div>
  );
}
