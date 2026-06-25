import { useEffect, useRef, useState } from "react";
import { api, SidecarError } from "@/api/client";

export type SidecarState = "ok" | "down" | "checking";

export interface SidecarHealth {
  state: SidecarState;
  lastError: string | null;
  lastChecked: number | null;
}

const INITIAL: SidecarHealth = {
  state: "checking",
  lastError: null,
  lastChecked: null,
};

export function useSidecarHealth(intervalMs = 5000): SidecarHealth {
  const [health, setHealth] = useState<SidecarHealth>(INITIAL);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const check = async () => {
      try {
        await api.health();
        if (mounted.current) {
          setHealth({ state: "ok", lastError: null, lastChecked: Date.now() });
        }
      } catch (err) {
        if (mounted.current) {
          const msg = err instanceof SidecarError ? err.message : "health check failed";
          setHealth({ state: "down", lastError: msg, lastChecked: Date.now() });
        }
      } finally {
        if (mounted.current) {
          timer = setTimeout(check, intervalMs);
        }
      }
    };
    check();

    return () => {
      mounted.current = false;
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs]);

  return health;
}
