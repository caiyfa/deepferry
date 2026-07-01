import { useRef, useEffect } from "react";

interface ProgressStep {
  step: string;
  message: string;
}

interface ExploreProgressProps {
  steps: ProgressStep[];
  isActive: boolean;
}

const KEYFRAMES = `
@keyframes df-slide-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes df-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.35; transform: scale(0.8); }
}
`;

type StepState = "completed" | "active" | "inactive";

function StepIcon({ state }: { state: StepState }) {
  const wrapper: React.CSSProperties = {
    width: "18px",
    height: "18px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };

  if (state === "completed") {
    return (
      <span
        style={{
          ...wrapper,
          background: "var(--df-green)",
          color: "var(--df-crust)",
          fontSize: "11px",
          fontWeight: 700,
          borderRadius: "50%",
        }}
      >
        ✓
      </span>
    );
  }

  if (state === "active") {
    return (
      <span style={wrapper}>
        <span
          style={{
            width: "10px",
            height: "10px",
            borderRadius: "50%",
            background: "var(--df-accent)",
            animation: "df-pulse 1s ease-in-out infinite",
          }}
        />
      </span>
    );
  }

  return (
    <span style={wrapper}>
      <span
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: "var(--df-fg-subtle)",
        }}
      />
    </span>
  );
}

export function ExploreProgress({ steps, isActive }: ExploreProgressProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const prevCount = useRef(steps.length);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || steps.length === prevCount.current) return;
    el.scrollTop = el.scrollHeight;
    prevCount.current = steps.length;
  }, [steps.length]);

  return (
    <>
      <style>{KEYFRAMES}</style>
      <div
        ref={containerRef}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--df-space-2)",
          overflowY: "auto",
        }}
      >
        {steps.map((s, i) => {
          const isLast = i === steps.length - 1;
          const isActiveStep = isActive && isLast;
          const state: StepState = isActiveStep ? "active" : "completed";

          return (
            <div
              key={`${s.step}-${i}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--df-space-2)",
                animation: "df-slide-in 0.2s ease",
              }}
            >
              <StepIcon state={state} />
              <span
                style={{
                  fontSize: "var(--df-fs-sm)",
                  lineHeight: "var(--df-lh-base)",
                  color: "var(--df-fg)",
                }}
              >
                {s.message}
              </span>
            </div>
          );
        })}
      </div>
    </>
  );
}
