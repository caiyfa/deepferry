import { SidecarError } from "@/api/client";

interface ErrorBannerProps {
  error: SidecarError | null;
  onDismiss?: () => void;
}

export function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  if (!error) return null;
  return (
    <div className="df-error df-fade-in" role="alert">
      <div className="df-row">
        <span className="df-error__code">{error.code}</span>
        {onDismiss ? (
          <button
            className="df-btn df-btn--ghost df-btn--sm df-btn--danger"
            onClick={onDismiss}
            aria-label="Dismiss error"
          >
            ✕
          </button>
        ) : null}
      </div>
      <div className="df-error__msg">{error.message}</div>
      {error.suggestion ? (
        <div className="df-error__hint">→ {error.suggestion}</div>
      ) : null}
    </div>
  );
}
