import { create } from "zustand";

export type AppMode = "explore" | "monitor" | "query";

interface ShellStore {
  activeMode: AppMode;
  selectedSources: string[];
  sidebarCollapsed: boolean;
  commandPaletteOpen: boolean;

  switchMode: (mode: AppMode) => void;
  setSelectedSources: (ids: string[]) => void;
  toggleSource: (id: string) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleCommandPalette: () => void;
  setCommandPaletteOpen: (open: boolean) => void;
}

export const useShellStore = create<ShellStore>()((set) => ({
  activeMode: "explore",
  selectedSources: [],
  sidebarCollapsed: false,
  commandPaletteOpen: false,

  switchMode: (mode) => set({ activeMode: mode }),
  setSelectedSources: (ids) => set({ selectedSources: ids }),
  toggleSource: (id) =>
    set((state) => {
      const selected = state.selectedSources.includes(id)
        ? state.selectedSources.filter((s) => s !== id)
        : [...state.selectedSources, id];
      // Always keep at least one source selected
      if (selected.length === 0) return state;
      return { selectedSources: selected };
    }),
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  toggleCommandPalette: () => set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
}));
