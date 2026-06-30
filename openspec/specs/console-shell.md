# Capability: Console Shell

> **Status**: planned | **Milestone**: M3 | **Owner**: frontend | **Depends on**: `mvp-init` M2, `config-panel`

## Summary

Replace the current 4-tab page router (`QueryPage / ResultsPage / HistoryPage / ExecutionPage`)
with a three-mode sidebar shell plus a command palette. Each mode loads its own page
lazily. The shell is format-agnostic — it provides the frame; modes provide the content.

## Motivation

- The current flat tab navigation does not scale to the 3+ modes designed in the
  console redesign. Sidebar with explicit icons + labels gives each mode a clear
  mental model.
- Power users need keyboard-first operation (`Ctrl+K` command palette, `Ctrl+1/2/3`
  mode switching). Tauri desktop users expect app-like navigation, not web-page tabs.
- Data source selection is currently a dropdown on the query page, invisible in other
  pages. Multi-select data sources for cross-source queries requires a persistent
  source panel in the sidebar.
- The status bar (connection health, agent count, latency) is currently absent.
  Operators need at-a-glance system status without opening a modal.

## Specification

### Sidebar

| Slot | Content |
|---|---|
| Top | Logo + app name |
| Mode section | 3 items: Explore `▌`, Monitor `▣` (with agent-count badge), Query `▸` |
| Source section | Data source list with health dots (green/yellow/red). Multi-select via toggle — clicking adds/removes from `selectedSources` set. Footer hint: "Select multiple for cross-source" |
| Saved section | Favorite queries (from `saved-queries` spec) |
| Footer | History link, Settings link |

Active mode highlighted with `$accent2` background. Sidebar collapses to icon-only at viewport < 1024px.

### Command Palette

Triggered by `Ctrl+K` (or `Cmd+K`). Modal overlay with:

- Search input (filter-as-you-type, fuzzy matching)
- Results grouped by section: Mode, Saved, Recent
- Each result shows name + keyboard shortcut
- Enter to activate, Escape to dismiss
- Click outside to dismiss

### Status Bar

Fixed 26px bar at bottom:

| Position | Content |
|---|---|
| Left | Data source dots (green if connected) |
| Right | Agent online count + "last activity" timestamp + version |

### State Model

```typescript
interface ShellState {
  activeMode: 'explore' | 'monitor' | 'query';
  selectedSources: Set<string>;   // multi-select, shared across modes
  isSidebarCollapsed: boolean;
  isCommandPaletteOpen: boolean;
}
```

`selectedSources` drives:
- Explore mode: auto-selects target sources for NL queries, shows cross-source hints
- Monitor mode: filters activity feed to matching sources
- Query mode: sets SQL editor context, enables DuckDB ATTACH syntax

### Routing

No URL-based routing for mode switching. Modes are pure React state transitions for
instantaneous switching. Deep-linking into a specific mode via URL hash is a nice-to-have
(P6 polish).

### Lazy Loading

Each mode page is loaded via `React.lazy()` + `Suspense`. The shell itself is always
eager-loaded.

## Acceptance Criteria

- [ ] Three modes switchable via sidebar click with < 100ms transition (no full page reload)
- [ ] `Ctrl+K` opens command palette; ESC dismisses; `Ctrl+1/2/3` switches modes
- [ ] Selecting mysql-main + finance-api in sidebar → `selectedSources` contains both
- [ ] Deselecting the last source is blocked (at least one source must remain selected)
- [ ] Agent online count badge updates when new WebSocket message arrives
- [ ] Sidebar auto-collapses at 1024px viewport; manual re-expand works
- [ ] Status bar shows green dot for mysql-main:3306 and finance-api (when running)
- [ ] All existing pages (Query, Results, History, Execution) are still reachable
  via URL hash during the transition period, but no longer in the main sidebar

## Out of Scope

- Actual implementation of Explore/Monitor/Query pages — these are separate specs
- WebSocket implementation — see `agent-monitor` spec
- Saved queries CRUD — see `query-enhancement` spec
- Cross-source query execution — already in `duckdb-cross-source.md`
