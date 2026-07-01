import { EmptyState } from "@/components/EmptyState";

export function MonitorPage() {
  return (
    <div className="df-page">
      <EmptyState
        glyph="▣"
        title="Agent monitor"
        sub="Live agent activity will appear here."
      />
    </div>
  );
}
