import { SIDECAR_URL, type FeedEvent, generateMockFeedEvent } from "./agents";

export type ConnectionState = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface AgentFeedHandle {
  disconnect: () => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const HEARTBEAT_MS = 25000;
const MAX_RECONNECT_ATTEMPTS = 5;

/**
 * Connect to the agent activity WebSocket at /ws/agents.
 * Falls back to mock mode (simulated events) if backend is unavailable.
 *
 * @param onEvent Called for each incoming feed event
 * @param onStateChange Called when connection state changes
 * @returns Handle with disconnect() to clean up
 */
export function connectAgentFeed(
  onEvent: (event: FeedEvent) => void,
  onStateChange: (state: ConnectionState) => void,
): AgentFeedHandle {
  const wsUrl = `${SIDECAR_URL.replace("http", "ws")}/ws/agents`;
  const useMock = !SIDECAR_URL || import.meta.env.VITE_AGENT_MOCK === "true";

  if (useMock) {
    return startMockFeed(onEvent, onStateChange);
  }

  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let shouldReconnect = true;
  let mockFallback: AgentFeedHandle | null = null;

  function setState(state: ConnectionState) {
    onStateChange(state);
  }

  function clearHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function connect() {
    setState(reconnectAttempts === 0 ? "connecting" : "reconnecting");

    try {
      ws = new WebSocket(wsUrl);
    } catch {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      reconnectAttempts = 0;
      setState("connected");
      clearHeartbeat();
      heartbeatTimer = setInterval(() => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, HEARTBEAT_MS);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string) as FeedEvent | { type: string };
        if ("type" in data && data.type === "pong") return;
        onEvent(data as FeedEvent);
      } catch {
      }
    };

    ws.onclose = () => {
      clearHeartbeat();
      if (shouldReconnect) {
        scheduleReconnect();
      } else {
        setState("disconnected");
      }
    };

    ws.onerror = () => {};
  }

  function scheduleReconnect() {
    if (!shouldReconnect) return;
    reconnectAttempts++;
    if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
      shouldReconnect = false;
      mockFallback = startMockFeed(onEvent, onStateChange);
      return;
    }
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts - 1),
      RECONNECT_MAX_MS,
    );
    setState("reconnecting");
    setTimeout(() => {
      if (shouldReconnect) connect();
    }, delay);
  }

  connect();

  return {
    disconnect: () => {
      shouldReconnect = false;
      clearHeartbeat();
      if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
      }
      if (mockFallback) {
        mockFallback.disconnect();
        mockFallback = null;
      }
      setState("disconnected");
    },
  };
}

// ── Mock feed simulator ──────────────────────────────────

function startMockFeed(
  onEvent: (event: FeedEvent) => void,
  onStateChange: (state: ConnectionState) => void,
): AgentFeedHandle {
  onStateChange("connecting");
  const connectTimer = setTimeout(() => onStateChange("connected"), 500);

  let intervalTimer: ReturnType<typeof setInterval> | null = null;

  function scheduleNext() {
    const delay = Math.random() * 3000 + 2000;
    intervalTimer = setTimeout(() => {
      onEvent(generateMockFeedEvent());
      scheduleNext();
    }, delay) as unknown as ReturnType<typeof setInterval>;
  }
  scheduleNext();

  return {
    disconnect: () => {
      clearTimeout(connectTimer);
      if (intervalTimer) {
        clearTimeout(intervalTimer as unknown as ReturnType<typeof setTimeout>);
      }
      onStateChange("disconnected");
    },
  };
}
