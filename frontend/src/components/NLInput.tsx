import { useState, useRef, useEffect } from "react";

interface NLInputProps {
  placeholder: string;
  disabled?: boolean;
  onSubmit: (question: string) => void;
}

/** Max textarea height in px — approx 5 rows at --df-fs-sm / --df-lh-base. */
const MAX_HEIGHT_PX = 120;

export function NLInput({ placeholder, disabled = false, onSubmit }: NLInputProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const isFocused = focused && !disabled;

  return (
    <>
      <style>{`.df-nl-input::placeholder { color: var(--df-fg-subtle); }`}</style>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          border: `1px solid ${isFocused ? "var(--df-accent)" : "var(--df-border)"}`,
          borderRadius: "var(--df-radius-md)",
          background: "var(--df-bg)",
          boxShadow: isFocused ? "0 0 0 3px var(--df-accent-soft)" : "none",
          transition:
            "border-color var(--df-dur-fast) var(--df-ease), box-shadow var(--df-dur-fast) var(--df-ease), opacity var(--df-dur-fast) var(--df-ease)",
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <textarea
          ref={textareaRef}
          className="df-nl-input"
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          style={{
            background: "transparent",
            color: "var(--df-fg)",
            fontSize: "var(--df-fs-sm)",
            fontFamily: "var(--df-font-sans)",
            lineHeight: "var(--df-lh-base)",
            padding: "var(--df-space-3)",
            width: "100%",
            resize: "none",
            border: "none",
            outline: "none",
            caretColor: disabled ? "transparent" : "var(--df-accent)",
          }}
        />
        <div
          style={{
            padding: "0 var(--df-space-3) var(--df-space-2)",
            fontSize: "var(--df-fs-xs)",
            color: "var(--df-fg-subtle)",
            fontFamily: "var(--df-font-mono)",
            letterSpacing: "var(--df-tracking-mono)",
            userSelect: "none",
          }}
        >
          Enter to send · Shift+Enter for newline
        </div>
      </div>
    </>
  );
}
