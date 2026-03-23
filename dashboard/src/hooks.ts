import { useCallback, useEffect, useRef, useState } from "react";

export interface TickUpdate {
  type: "tick_update";
  tick: number;
  world_time: string;
}

/**
 * WebSocket hook for dashboard tick updates.
 * Reconnects with exponential backoff. Cleans up on unmount.
 */
export function useTickStream(onTick: (update: TickUpdate) => void) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timeoutRef = useRef<number>(0);
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/dashboard`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as TickUpdate;
        if (data.type === "tick_update" || data.type === "micro_tick") {
          onTickRef.current(data);
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
      retriesRef.current++;
      timeoutRef.current = window.setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(timeoutRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return connected;
}

/** Hook to track tick history for charts. */
export function useTickHistory(maxPoints = 60) {
  const [history, setHistory] = useState<
    { tick: number; agents: number; trades: number }[]
  >([]);

  const addPoint = useCallback(
    (tick: number, agents: number, trades: number) => {
      setHistory((prev) => {
        const next = [...prev, { tick, agents, trades }];
        return next.length > maxPoints ? next.slice(-maxPoints) : next;
      });
    },
    [maxPoints]
  );

  return { history, addPoint };
}
