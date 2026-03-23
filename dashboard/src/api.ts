import type { Agent, WorldStatus } from "./types";

const API_BASE = "/api/v1";

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    // Parse server error details (FastAPI returns {detail: "..."})
    const body = await res.json().catch(() => null);
    const detail = body?.detail ?? `${res.status} ${res.statusText}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export function getWorldStatus(): Promise<WorldStatus> {
  return fetchJSON("/world/status");
}

export function getAgents(limit = 50, offset = 0): Promise<Agent[]> {
  return fetchJSON(`/agents?limit=${limit}&offset=${offset}`);
}

export interface WorldEvent {
  id: string;
  tick: number;
  category: string;
  event_type: string;
  description: string;
  agent_id: string | null;
  timestamp: string;
}

export function getEvents(limit = 50): Promise<WorldEvent[]> {
  return fetchJSON(`/events?limit=${limit}`);
}

// --- Seasons ---

export interface Season {
  id: string;
  name: string;
  description: string;
  status: string;
  theme: string;
  rules: Record<string, unknown>;
  start_tick: number | null;
  end_tick: number | null;
  max_agents: number;
}

export interface LeaderboardEntry {
  rank: number;
  agent_id: string;
  name: string;
  title: string | null;
  balance: number;
  total_trades: number;
  reputation: number;
}

export async function getCurrentSeason(): Promise<Season | null> {
  try {
    return await fetchJSON("/seasons/current");
  } catch {
    return null;
  }
}

export function getLeaderboard(seasonId: string): Promise<LeaderboardEntry[]> {
  return fetchJSON(`/seasons/${seasonId}/leaderboard`);
}

// --- Auth ---

interface AuthResponse {
  access_token: string;
  token_type: string;
  user: { id: string; username: string; email: string };
}

export async function registerUser(
  email: string,
  username: string,
  password: string
): Promise<AuthResponse> {
  return fetchJSON("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password }),
  });
}

export async function loginUser(
  email: string,
  password: string
): Promise<AuthResponse> {
  return fetchJSON("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function createAgent(
  token: string,
  name: string,
  title: string,
  bio: string
): Promise<{ agent: Agent; token: string }> {
  return fetchJSON("/agents", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ name, title, bio }),
  });
}
