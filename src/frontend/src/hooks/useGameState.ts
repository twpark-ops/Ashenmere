import { useState, useEffect, useCallback, useRef } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const WS_BASE = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';

export interface AgentState {
  id: string;
  name: string;
  title: string | null;
  x: number;
  y: number;
  balance: number;
  reputation: number;
  status: string;
  location: string;
}

export interface WorldState {
  tick: number;
  world_time: string;
  time_of_day: string;
  total_agents: number;
  active_agents: number;
  total_trades: number;
}

export interface GameEvent {
  id: string;
  tick: number;
  category: string;
  event_type: string;
  description: string;
  agent_name: string | null;
  timestamp: string;
}

// Replace Convex useQuery with REST polling + WebSocket
export function useWorldState() {
  const [state, setState] = useState<WorldState | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/world/status`);
      if (res.ok) setState(await res.json());
    } catch { /* retry on next interval */ }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return state;
}

export function useAgents() {
  const [agents, setAgents] = useState<AgentState[]>([]);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/agents`);
      if (res.ok) setAgents(await res.json());
    } catch { /* retry */ }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  return agents;
}

export function useTickStream(onTick: (data: any) => void) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  const connect = useCallback(() => {
    const ws = new WebSocket(`${WS_BASE}/ws/dashboard`);
    wsRef.current = ws;

    ws.onopen = () => { setConnected(true); retriesRef.current = 0; };
    ws.onmessage = (e) => {
      try { onTickRef.current(JSON.parse(e.data)); } catch {}
    };
    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
      retriesRef.current++;
      setTimeout(connect, delay);
    };
    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  return connected;
}

export function useEvents(limit = 50) {
  const [events, setEvents] = useState<GameEvent[]>([]);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/events?limit=${limit}`);
      if (res.ok) setEvents(await res.json());
    } catch { /* retry */ }
  }, [limit]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  return events;
}
