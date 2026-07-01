import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type CSSProperties,
} from "react";
import { useShellStore } from "@/store/shell";
import { exploreApi } from "@/api/explore";
import type { ChatMessage } from "@/api/explore";
import { NLInput } from "@/components/NLInput";
import { ExploreProgress } from "@/components/ExploreProgress";
import { ExploreError } from "@/components/ExploreError";
import { ExploreResult } from "@/components/ExploreResult";
import { EmptyState } from "@/components/EmptyState";

interface ProgressStep {
  step: string;
  message: string;
}

export function ExplorePage() {
  const selectedSources = useShellStore((s) => s.selectedSources);
  const switchMode = useShellStore((s) => s.switchMode);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progressSteps, setProgressSteps] = useState<ProgressStep[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);

  const messagesRef = useRef<HTMLDivElement>(null);
  // Mirror messages into a ref so the async generator loop reads the latest
  // history without a stale-closure over `messages`.
  const messagesRefState = useRef<ChatMessage[]>(messages);
  messagesRefState.current = messages;

  useEffect(() => {
    let cancelled = false;
    exploreApi.getSuggestions(selectedSources).then((s) => {
      if (!cancelled) setSuggestions(s);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedSources]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, progressSteps, isStreaming, error]);

  const placeholder =
    selectedSources.length === 0
      ? "Select a data source to start exploring…"
      : selectedSources.length === 1
        ? "Ask a question about your data…"
        : `Ask across ${selectedSources.length} sources — cross-source queries enabled…`;

  const handleSubmit = useCallback(
    async (question: string) => {
      if (isStreaming) return;
      setError(null);
      setProgressSteps([]);

      const userMsg: ChatMessage = { role: "user", content: question };
      const priorHistory = messagesRefState.current;
      setMessages((prev) => [...prev, userMsg]);
      setIsStreaming(true);

      try {
        const gen = exploreApi.ask(question, selectedSources, priorHistory);
        for await (const event of gen) {
          if (event.step === "done") {
            const assistantMsg: ChatMessage = {
              role: "assistant",
              content: event.message,
              result: event.result,
            };
            setMessages((prev) => [...prev, assistantMsg]);
            setProgressSteps([]);
          } else if (event.step === "error") {
            setError(event.error ?? event.message);
          } else {
            setProgressSteps((prev) => [
              ...prev,
              { step: event.step, message: event.message },
            ]);
          }
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Unexpected error";
        setError(msg);
      } finally {
        setIsStreaming(false);
      }
    },
    [isStreaming, selectedSources],
  );

  const handleRetry = useCallback(() => {
    setError(null);
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (lastUser) {
      void handleSubmit(lastUser.content);
    }
  }, [messages, handleSubmit]);

  const handleSwitchToQuery = useCallback(() => {
    switchMode("query");
  }, [switchMode]);

  const showEmptyState =
    messages.length === 0 && !isStreaming && !error;

  const pageStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    minHeight: 0,
  };

  const messagesAreaStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    padding: "var(--df-space-4)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--df-space-4)",
  };

  const inputAreaStyle: CSSProperties = {
    flexShrink: 0,
    padding: "var(--df-space-3) var(--df-space-4)",
    borderTop: "1px solid var(--df-border)",
  };

  const userBubbleStyle: CSSProperties = {
    alignSelf: "flex-end",
    maxWidth: "70%",
    padding: "var(--df-space-2) var(--df-space-3)",
    background: "var(--df-accent-soft)",
    borderRadius: "var(--df-radius-md)",
    fontSize: "var(--df-fs-sm)",
    color: "var(--df-fg)",
    lineHeight: "var(--df-lh-base)",
    fontFamily: "var(--df-font-sans)",
    wordBreak: "break-word",
  };

  const assistantSectionStyle: CSSProperties = {
    alignSelf: "stretch",
    display: "flex",
    flexDirection: "column",
    gap: "var(--df-space-3)",
  };

  const assistantTextStyle: CSSProperties = {
    color: "var(--df-fg)",
    fontSize: "var(--df-fs-sm)",
    lineHeight: "var(--df-lh-base)",
    fontFamily: "var(--df-font-sans)",
  };

  const emptyWrapStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    gap: "var(--df-space-5)",
    padding: "var(--df-space-4)",
  };

  const chipsWrapStyle: CSSProperties = {
    display: "inline-flex",
    gap: "var(--df-space-2)",
    flexWrap: "wrap",
    justifyContent: "center",
    maxWidth: "640px",
  };

  const chipStyle: CSSProperties = {
    padding: "var(--df-space-2) var(--df-space-3)",
    border: "1px solid var(--df-border)",
    borderRadius: "var(--df-radius-pill)",
    background: "var(--df-bg-elevated)",
    cursor: "pointer",
    fontSize: "var(--df-fs-sm)",
    color: "var(--df-fg)",
    fontFamily: "var(--df-font-sans)",
    transition:
      "border-color var(--df-dur-fast) var(--df-ease), background var(--df-dur-fast) var(--df-ease)",
  };

  const sourceWarningStyle: CSSProperties = {
    color: "var(--df-fg-subtle)",
    fontSize: "var(--df-fs-sm)",
    fontFamily: "var(--df-font-sans)",
    textAlign: "center",
  };

  return (
    <div style={pageStyle}>
      <div ref={messagesRef} style={messagesAreaStyle}>
        {showEmptyState ? (
          <div style={emptyWrapStyle}>
            <EmptyState
              glyph="▌"
              title="Explore your data"
              sub="Ask questions in natural language"
            />
            {selectedSources.length === 0 ? (
              <div style={sourceWarningStyle}>
                Select at least one data source in the sidebar to begin.
              </div>
            ) : (
              <div style={chipsWrapStyle}>
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    style={chipStyle}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = "var(--df-accent)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = "var(--df-border)";
                    }}
                    onClick={() => void handleSubmit(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={`u-${i}`} style={userBubbleStyle}>
                  {m.content}
                </div>
              ) : (
                <div key={`a-${i}`} style={assistantSectionStyle}>
                  <div style={assistantTextStyle}>{m.content}</div>
                  {m.result ? (
                    <ExploreResult
                      result={m.result}
                      sourceLabels={selectedSources}
                    />
                  ) : null}
                </div>
              ),
            )}

            {isStreaming ? (
              <ExploreProgress steps={progressSteps} isActive={true} />
            ) : null}

            {error ? (
              <ExploreError
                message={error}
                onRetry={handleRetry}
                onSwitchToQuery={handleSwitchToQuery}
              />
            ) : null}
          </>
        )}
      </div>

      <div style={inputAreaStyle}>
        <NLInput
          placeholder={placeholder}
          disabled={isStreaming}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  );
}