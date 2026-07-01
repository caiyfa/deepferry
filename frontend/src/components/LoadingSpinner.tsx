interface LoadingSpinnerProps {
  size?: number;
  label?: string;
}

export function LoadingSpinner({ size = 24, label }: LoadingSpinnerProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "var(--df-space-2)",
        padding: "var(--df-space-4)",
      }}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        style={{ animation: "spin 0.8s linear infinite" }}
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="var(--df-border-strong)"
          strokeWidth="2.5"
          opacity="0.25"
        />
        <path
          d="M12 2a10 10 0 0 1 10 10"
          stroke="var(--df-accent)"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
      </svg>
      {label && (
        <span style={{ color: "var(--df-fg-subtle)", fontSize: "var(--df-fs-sm)" }}>
          {label}
        </span>
      )}
    </div>
  );
}
