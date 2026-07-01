import { lazy, Suspense, useEffect } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { QueryProvider } from "@/context/QueryContext";
import { Sidebar } from "@/components/Sidebar";
import { StatusBar } from "@/components/StatusBar";
import { CommandPalette } from "@/components/CommandPalette";
import { useShellStore, type AppMode } from "@/store/shell";

// Lazy-load the three primary mode pages (code-split per mode)
const ExplorePage = lazy(() =>
  import("@/pages/ExplorePage").then((m) => ({ default: m.ExplorePage })),
);
const MonitorPage = lazy(() =>
  import("@/pages/MonitorPage").then((m) => ({ default: m.MonitorPage })),
);
const QueryPage = lazy(() =>
  import("@/pages/QueryPage").then((m) => ({ default: m.QueryPage })),
);

// Legacy pages — kept for backward compatibility under /legacy/*
const LegacyResultsPage = lazy(() =>
  import("@/pages/ResultsPage").then((m) => ({ default: m.ResultsPage })),
);
const LegacyHistoryPage = lazy(() =>
  import("@/pages/HistoryPage").then((m) => ({ default: m.HistoryPage })),
);
const LegacyExecutionPage = lazy(() =>
  import("@/pages/ExecutionPage").then((m) => ({ default: m.ExecutionPage })),
);

function PageFallback() {
  return (
    <div className="df-page" style={{ padding: "var(--df-space-5)" }}>
      <div className="df-skeleton">
        <div className="df-skeleton__line" />
        <div className="df-skeleton__line" />
        <div className="df-skeleton__line" />
      </div>
    </div>
  );
}

function ModeView() {
  const activeMode = useShellStore((s) => s.activeMode);
  return (
    <Suspense fallback={<PageFallback />}>
      {activeMode === "monitor" ? (
        <MonitorPage />
      ) : activeMode === "query" ? (
        <QueryPage />
      ) : (
        <ExplorePage />
      )}
    </Suspense>
  );
}

export default function App() {
  const commandPaletteOpen = useShellStore((s) => s.commandPaletteOpen);
  const setCommandPaletteOpen = useShellStore((s) => s.setCommandPaletteOpen);
  const switchMode = useShellStore((s) => s.switchMode);

  // Global keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;

      // Ctrl+K toggles command palette
      if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        setCommandPaletteOpen(!commandPaletteOpen);
        return;
      }

      // Ctrl+1/2/3 switches modes
      if (e.key === "1") {
        e.preventDefault();
        switchMode("explore" as AppMode);
      } else if (e.key === "2") {
        e.preventDefault();
        switchMode("monitor" as AppMode);
      } else if (e.key === "3") {
        e.preventDefault();
        switchMode("query" as AppMode);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commandPaletteOpen, setCommandPaletteOpen, switchMode]);

  return (
    <QueryProvider>
      <HashRouter>
        <div className="df-app">
          <Sidebar />
          <main className="df-main">
            <Routes>
              {/* Primary shell: state-based mode switching, no URL change */}
              <Route path="/" element={<ModeView />} />

              {/* Legacy routes for backward compatibility */}
              <Route
                path="/legacy/results"
                element={
                  <Suspense fallback={<PageFallback />}>
                    <LegacyResultsPage />
                  </Suspense>
                }
              />
              <Route
                path="/legacy/history"
                element={
                  <Suspense fallback={<PageFallback />}>
                    <LegacyHistoryPage />
                  </Suspense>
                }
              />
              <Route
                path="/legacy/history/:id"
                element={
                  <Suspense fallback={<PageFallback />}>
                    <LegacyExecutionPage />
                  </Suspense>
                }
              />

              {/* Fallback */}
              <Route path="*" element={<ModeView />} />
            </Routes>
          </main>
          <StatusBar />

          <CommandPalette
            isOpen={commandPaletteOpen}
            onClose={() => setCommandPaletteOpen(false)}
          />
        </div>
      </HashRouter>
    </QueryProvider>
  );
}
