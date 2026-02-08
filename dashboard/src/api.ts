import type { Agent, WorldStatus } from "./types";

const API_BASE = "/api/v1";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export function getWorldStatus(): Promise<WorldStatus> {
  return fetchJSON("/world/status");
}

export function getAgents(limit = 50, offset = 0): Promise<Agent[]> {
  return fetchJSON(`/agents?limit=${limit}&offset=${offset}`);
}

export function getAgent(id: string): Promise<Agent> {
  return fetchJSON(`/agents/${id}`);
}
