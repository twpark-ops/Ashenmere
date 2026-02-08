import { useCallback, useEffect, useState } from "react";
import { getAgents, getWorldStatus } from "./api";
import type { Agent, WorldStatus } from "./types";

function StatusCards({ status }: { status: WorldStatus }) {
  return (
    <div className="grid">
      <div className="card">
        <h2>World Tick</h2>
        <div className="stat">{status.tick.toLocaleString()}</div>
        <div className="stat-label">{status.world_time}</div>
      </div>
      <div className="card">
        <h2>Agents</h2>
        <div className="stat">{status.active_agents}</div>
        <div className="stat-label">active / {status.total_agents} total</div>
      </div>
      <div className="card">
        <h2>Total Trades</h2>
        <div className="stat">{status.total_trades.toLocaleString()}</div>
        <div className="stat-label">completed transactions</div>
      </div>
    </div>
  );
}

function formatBalance(cents: number): string {
  const dollars = cents / 100;
  return dollars.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function AgentTable({ agents }: { agents: Agent[] }) {
  return (
    <div className="card">
      <h2>Agents</h2>
      <table className="agent-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Title</th>
            <th>Tier</th>
            <th>Status</th>
            <th>Balance</th>
            <th>Reputation</th>
            <th>Location</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.id}>
              <td>{agent.name}</td>
              <td>{agent.title ?? "—"}</td>
              <td>
                <span className={`badge badge--${agent.tier}`}>{agent.tier}</span>
              </td>
              <td>
                <span className={`badge badge--${agent.status}`}>{agent.status}</span>
              </td>
              <td>
                <span className="money">${formatBalance(agent.balance)}</span>
              </td>
              <td>{agent.reputation}</td>
              <td>{agent.location}</td>
            </tr>
          ))}
          {agents.length === 0 && (
            <tr>
              <td colSpan={7} style={{ textAlign: "center", color: "var(--text-dim)" }}>
                No agents yet. Start some agents to see them here.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function App() {
  const [status, setStatus] = useState<WorldStatus | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([getWorldStatus(), getAgents()]);
      setStatus(s);
      setAgents(a);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div className="app">
      <header>
        <h1>
          Agent<span>Burg</span>
        </h1>
        {status && <span className="tick-badge">Tick #{status.tick}</span>}
      </header>

      {error && <div className="error">Error: {error}</div>}
      {!status && !error && <div className="loading">Connecting to server...</div>}

      {status && <StatusCards status={status} />}
      <AgentTable agents={agents} />
    </div>
  );
}
