import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getAgents, getWorldStatus } from "./api";
import { useTickHistory } from "./hooks";
import type { Agent, WorldStatus } from "./types";

// --- Status Cards ---

function StatusCards({
  status,
  lastUpdate,
}: {
  status: WorldStatus;
  lastUpdate: Date | null;
}) {
  const timeAgo = lastUpdate
    ? `${Math.round((Date.now() - lastUpdate.getTime()) / 1000)}s ago`
    : "—";

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
        <div className="stat-label">
          active / {status.total_agents} total
        </div>
      </div>
      <div className="card">
        <h2>Total Trades</h2>
        <div className="stat">{status.total_trades.toLocaleString()}</div>
        <div className="stat-label">updated {timeAgo}</div>
      </div>
    </div>
  );
}

// --- Economy Chart ---

function EconomyChart({
  history,
}: {
  history: { tick: number; agents: number; trades: number }[];
}) {
  if (history.length < 2) {
    return (
      <div className="card chart-section">
        <h2>Economy Activity</h2>
        <div
          className="loading"
          style={{ padding: "40px 0", fontSize: "13px" }}
        >
          Collecting data points...
        </div>
      </div>
    );
  }

  return (
    <div className="card chart-section">
      <h2>Economy Activity</h2>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart
          data={history}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <defs>
            <linearGradient id="colorTrades" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
          <XAxis
            dataKey="tick"
            tick={{ fill: "#8888a0", fontSize: 11 }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#8888a0", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "#1a1a24",
              border: "1px solid #2a2a3a",
              borderRadius: 8,
              fontSize: 13,
            }}
          />
          <Area
            type="monotone"
            dataKey="trades"
            name="Cumulative Trades"
            stroke="#6366f1"
            fill="url(#colorTrades)"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Balance Distribution Chart ---

function BalanceDistribution({ agents }: { agents: Agent[] }) {
  const buckets = useMemo(() => {
    const ranges = [
      { label: "$0-50", min: 0, max: 5000 },
      { label: "$50-100", min: 5000, max: 10000 },
      { label: "$100-500", min: 10000, max: 50000 },
      { label: "$500-1K", min: 50000, max: 100000 },
      { label: "$1K+", min: 100000, max: Infinity },
    ];
    return ranges.map((r) => ({
      range: r.label,
      count: agents.filter((a) => a.balance >= r.min && a.balance < r.max)
        .length,
    }));
  }, [agents]);

  if (agents.length === 0) return null;

  return (
    <div className="card chart-section">
      <h2>Wealth Distribution</h2>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart
          data={buckets}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
          <XAxis
            dataKey="range"
            tick={{ fill: "#8888a0", fontSize: 11 }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#8888a0", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={{
              background: "#1a1a24",
              border: "1px solid #2a2a3a",
              borderRadius: 8,
              fontSize: 13,
            }}
          />
          <Bar dataKey="count" name="Agents" fill="#22c55e" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Agent Table ---

type SortField = "name" | "balance" | "reputation" | "status";
type SortDir = "asc" | "desc";

function formatBalance(cents: number): string {
  const dollars = cents / 100;
  return dollars.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function AgentTable({ agents }: { agents: Agent[] }) {
  const [sortField, setSortField] = useState<SortField>("balance");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    return [...agents].sort((a, b) => {
      const dir = sortDir === "asc" ? 1 : -1;
      if (sortField === "name") return dir * a.name.localeCompare(b.name);
      if (sortField === "balance") return dir * (a.balance - b.balance);
      if (sortField === "reputation")
        return dir * (a.reputation - b.reputation);
      if (sortField === "status")
        return dir * a.status.localeCompare(b.status);
      return 0;
    });
  }, [agents, sortField, sortDir]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const sortIcon = (field: SortField) => {
    if (sortField !== field) return "";
    return sortDir === "asc" ? " \u25B2" : " \u25BC";
  };

  return (
    <div className="card">
      <h2>
        Agents{" "}
        <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>
          ({agents.length})
        </span>
      </h2>
      <table className="agent-table">
        <thead>
          <tr>
            <th
              onClick={() => toggleSort("name")}
              style={{ cursor: "pointer" }}
            >
              Name{sortIcon("name")}
            </th>
            <th>Title</th>
            <th>Tier</th>
            <th
              onClick={() => toggleSort("status")}
              style={{ cursor: "pointer" }}
            >
              Status{sortIcon("status")}
            </th>
            <th
              onClick={() => toggleSort("balance")}
              style={{ cursor: "pointer" }}
            >
              Balance{sortIcon("balance")}
            </th>
            <th
              onClick={() => toggleSort("reputation")}
              style={{ cursor: "pointer" }}
            >
              Reputation{sortIcon("reputation")}
            </th>
            <th>Location</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((agent) => (
            <tr key={agent.id}>
              <td>{agent.name}</td>
              <td>{agent.title ?? "\u2014"}</td>
              <td>
                <span className={`badge badge--${agent.tier}`}>
                  {agent.tier}
                </span>
              </td>
              <td>
                <span className={`badge badge--${agent.status}`}>
                  {agent.status}
                </span>
              </td>
              <td>
                <span
                  className={`money ${agent.balance >= 0 ? "money--positive" : "money--negative"}`}
                >
                  ${formatBalance(agent.balance)}
                </span>
              </td>
              <td>{agent.reputation}</td>
              <td>{agent.location}</td>
            </tr>
          ))}
          {agents.length === 0 && (
            <tr>
              <td
                colSpan={7}
                style={{ textAlign: "center", color: "var(--text-dim)" }}
              >
                No agents yet. Start some agents to see them here.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// --- Main App ---

export function App() {
  const [status, setStatus] = useState<WorldStatus | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const { history, addPoint } = useTickHistory(60);

  const refresh = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([getWorldStatus(), getAgents()]);
      setStatus(s);
      setAgents(a);
      setError(null);
      setLastUpdate(new Date());
      addPoint(s.tick, s.active_agents, s.total_trades);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, [addPoint]);

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
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {status && <span className="tick-badge">Tick #{status.tick}</span>}
        </div>
      </header>

      {error && <div className="error">Error: {error}</div>}
      {!status && !error && (
        <div className="loading">Connecting to server...</div>
      )}

      {status && <StatusCards status={status} lastUpdate={lastUpdate} />}

      <div className="charts-grid">
        <EconomyChart history={history} />
        <BalanceDistribution agents={agents} />
      </div>

      <AgentTable agents={agents} />
    </div>
  );
}
