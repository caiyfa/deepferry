import { useState, useRef, useEffect, useMemo } from "react";
import { useShellStore, type AppMode } from "@/store/shell";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

interface CommandItem {
  id: string;
  name: string;
  icon: string;
  shortcut?: string;
  action: () => void;
}

interface CommandGroup {
  id: string;
  label: string;
  items: CommandItem[];
}

/** CommandPalette — Ctrl+K overlay. Mounted by App.tsx; open state flows
 * from useShellStore().commandPaletteOpen via the `isOpen` prop. */
export function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const switchMode = useShellStore((s) => s.switchMode);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
  const activeMode = useShellStore((s) => s.activeMode);

  const [searchQuery, setSearchQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const recentQueries: string[] = [];

  const groups: CommandGroup[] = useMemo(() => {
    const runAndClose = (fn: () => void) => () => {
      fn();
      onClose();
    };

    return [
      {
        id: "modes",
        label: "Modes",
        items: [
          {
            id: "mode-explore",
            name: "Switch to Explore",
            icon: "◎",
            shortcut: "Ctrl+1",
            action: runAndClose(() => switchMode("explore" as AppMode)),
          },
          {
            id: "mode-monitor",
            name: "Switch to Monitor",
            icon: "◉",
            shortcut: "Ctrl+2",
            action: runAndClose(() => switchMode("monitor" as AppMode)),
          },
          {
            id: "mode-query",
            name: "Switch to Query",
            icon: "▸",
            shortcut: "Ctrl+3",
            action: runAndClose(() => switchMode("query" as AppMode)),
          },
        ],
      },
      {
        id: "actions",
        label: "Actions",
        items: [
          {
            id: "action-toggle-sidebar",
            name: "Toggle Sidebar",
            icon: "▤",
            shortcut: "Ctrl+B",
            action: runAndClose(() => toggleSidebar()),
          },
          {
            id: "action-refresh-sources",
            name: "Refresh Sources",
            icon: "⟳",
            shortcut: "Ctrl+R",
            action: runAndClose(() => {
              void 0;
            }),
          },
        ],
      },
      {
        id: "recent",
        label: "Recent",
        items:
          recentQueries.length > 0
            ? recentQueries.map((q, i) => ({
                id: `recent-${i}`,
                name: q,
                icon: "⌖",
                action: runAndClose(() => {
                  void 0;
                }),
              }))
            : [
                {
                  id: "recent-empty",
                  name: "No recent queries",
                  icon: "∅",
                  action: () => {},
                },
              ],
      },
    ];
  }, [switchMode, toggleSidebar, onClose, recentQueries]);

  // Subsequence fuzzy match: every char of the query must appear in order
  // within the item name (case-insensitive). Cheap and sufficient here.
  const filteredGroups: CommandGroup[] = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return groups;

    const matches = (name: string) => {
      const n = name.toLowerCase();
      let qi = 0;
      for (let ni = 0; ni < n.length && qi < q.length; ni++) {
        if (n[ni] === q[qi]) qi++;
      }
      return qi === q.length;
    };

    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((it) => matches(it.name)),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, searchQuery]);

  const flatItems = useMemo(
    () => filteredGroups.flatMap((g) => g.items),
    [filteredGroups],
  );

  useEffect(() => {
    if (isOpen) {
      setSearchQuery("");
      setActiveIndex(0);
    }
  }, [isOpen]);

  // Focus the input on open.
  useEffect(() => {
    if (isOpen) {
      // Defer to next tick so the overlay is painted first.
      const t = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
  }, [isOpen]);

  // Clamp activeIndex when the filtered list shrinks.
  useEffect(() => {
    if (activeIndex > 0 && activeIndex >= flatItems.length) {
      setActiveIndex(Math.max(0, flatItems.length - 1));
    }
  }, [flatItems.length, activeIndex]);

  // Scroll the active row into view.
  useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(
      `[data-cp-index="${activeIndex}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (!isOpen) return null;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, flatItems.length - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const item = flatItems[activeIndex];
      item?.action();
      return;
    }
  };

  // Build a map from item id → flat index for rendering highlights.
  let runningIndex = 0;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0, 0, 0, 0.5)",
        backdropFilter: "blur(2px)",
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start",
        animation: "df-fade-in var(--df-dur-fast) var(--df-ease) both",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
        style={{
          marginTop: "15vh",
          width: "min(560px, calc(100vw - 2rem))",
          maxHeight: "min(70vh, 560px)",
          display: "flex",
          flexDirection: "column",
          background: "var(--df-bg-elevated)",
          border: "1px solid var(--df-border-strong)",
          borderRadius: "var(--df-radius-sm)",
          boxShadow: "var(--df-shadow-lg)",
          overflow: "hidden",
        }}
      >
        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--df-space-3)",
            padding: `var(--df-space-3) var(--df-space-4)`,
            borderBottom: "1px solid var(--df-border)",
            background: "var(--df-bg-sunken)",
          }}
        >
          <span
            aria-hidden
            style={{
              color: "var(--df-fg-subtle)",
              fontSize: "var(--df-fs-md)",
              lineHeight: 1,
            }}
          >
            ⌘
          </span>
          <input
            ref={inputRef}
            type="text"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setActiveIndex(0);
            }}
            placeholder="Type a command or search…"
            aria-label="Search commands"
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--df-fg)",
              fontSize: "var(--df-fs-md)",
              fontFamily: "var(--df-font-sans)",
              letterSpacing: "var(--df-tracking-tight)",
            }}
          />
          <kbd
            style={{
              fontFamily: "var(--df-font-mono)",
              fontSize: "var(--df-fs-xs)",
              color: "var(--df-fg-subtle)",
              padding: "2px var(--df-space-2)",
              border: "1px solid var(--df-border-strong)",
              borderRadius: "var(--df-radius-xs)",
              background: "var(--df-bg)",
            }}
          >
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div
          ref={listRef}
          style={{
            maxHeight: 400,
            overflowY: "auto",
            padding: "var(--df-space-2) 0",
          }}
        >
          {flatItems.length === 0 ? (
            <div
              style={{
                padding: "var(--df-space-6) var(--df-space-4)",
                textAlign: "center",
                color: "var(--df-fg-subtle)",
                fontSize: "var(--df-fs-sm)",
              }}
            >
              No matching commands
            </div>
          ) : (
            filteredGroups.map((group) => {
              const groupItems = group.items.map((item) => {
                const idx = runningIndex++;
                const isActive = idx === activeIndex;
                const isCurrentMode =
                  group.id === "modes" &&
                  activeMode ===
                    (item.id === "mode-explore"
                      ? "explore"
                      : item.id === "mode-monitor"
                        ? "monitor"
                        : item.id === "mode-query"
                          ? "query"
                          : null);
                return (
                  <button
                    key={item.id}
                    type="button"
                    data-cp-index={idx}
                    onMouseEnter={() => setActiveIndex(idx)}
                    onClick={() => item.action()}
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--df-space-3)",
                      padding: `var(--df-space-2) var(--df-space-4)`,
                      textAlign: "left",
                      background: isActive
                        ? "var(--df-accent-soft)"
                        : "transparent",
                      borderLeft: isActive
                        ? "2px solid var(--df-accent)"
                        : "2px solid transparent",
                      color: isActive
                        ? "var(--df-fg)"
                        : "var(--df-fg-muted)",
                      cursor: "pointer",
                      transition:
                        "background var(--df-dur-fast) var(--df-ease), border-color var(--df-dur-fast) var(--df-ease)",
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: "1.25em",
                        textAlign: "center",
                        color: isActive
                          ? "var(--df-accent)"
                          : "var(--df-fg-subtle)",
                        fontSize: "var(--df-fs-md)",
                        lineHeight: 1,
                      }}
                    >
                      {item.icon}
                    </span>
                    <span
                      style={{
                        flex: 1,
                        fontSize: "var(--df-fs-sm)",
                        fontWeight: isCurrentMode ? 600 : 400,
                      }}
                    >
                      {item.name}
                      {isCurrentMode && (
                        <span
                          style={{
                            marginLeft: "var(--df-space-2)",
                            color: "var(--df-accent)",
                            fontSize: "var(--df-fs-xs)",
                          }}
                        >
                          · current
                        </span>
                      )}
                    </span>
                    {item.shortcut && (
                      <span
                        style={{
                          fontFamily: "var(--df-font-mono)",
                          fontSize: "var(--df-fs-xs)",
                          color: "var(--df-fg-subtle)",
                          letterSpacing: "var(--df-tracking-mono)",
                        }}
                      >
                        {item.shortcut}
                      </span>
                    )}
                  </button>
                );
              });

              return (
                <div key={group.id} role="group">
                  <div
                    style={{
                      padding: `var(--df-space-3) var(--df-space-4) var(--df-space-1)`,
                      fontSize: "var(--df-fs-xs)",
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: "0.14em",
                      color: "var(--df-fg-subtle)",
                    }}
                  >
                    {group.label}
                  </div>
                  {groupItems}
                </div>
              );
            })
          )}
        </div>

        {/* Footer hint */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: `var(--df-space-2) var(--df-space-4)`,
            borderTop: "1px solid var(--df-border)",
            background: "var(--df-bg-sunken)",
            fontSize: "var(--df-fs-xs)",
            color: "var(--df-fg-subtle)",
            fontFamily: "var(--df-font-mono)",
          }}
        >
          <span>↑↓ navigate</span>
          <span>↵ run</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}