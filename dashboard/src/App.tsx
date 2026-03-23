import { useCallback, useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  createAgent,
  getAgents,
  getCurrentSeason,
  getEvents,
  getLeaderboard,
  getWorldStatus,
  registerUser,
} from "./api";
import type { LeaderboardEntry, Season, WorldEvent } from "./api";
import { useTickHistory, useTickStream } from "./hooks";
import type { Agent, WorldStatus } from "./types";

// --- Time of Day ---

const TIME_ICONS: Record<string, string> = {
  morning: "\u{1F305}",
  afternoon: "\u{2600}\u{FE0F}",
  evening: "\u{1F306}",
  night: "\u{1F319}",
};

// --- Season Header ---

function SeasonHeader({
  season,
  status,
}: {
  season: Season | null;
  status: WorldStatus | null;
}) {
  if (!season || !status) return null;

  const startTick = season.start_tick ?? 0;
  const endTick = season.end_tick ?? startTick + 1008;
  const totalTicks = endTick - startTick;
  const elapsed = Math.max(0, status.tick - startTick);
  const progress = Math.min(100, (elapsed / totalTicks) * 100);
  const daysLeft = Math.max(0, Math.ceil((totalTicks - elapsed) / 6));

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{season.name}</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          Day {status.day} / {Math.ceil(totalTicks / 6)} &middot; {daysLeft} days left
        </span>
      </div>
      <div style={{ background: "var(--bg)", borderRadius: 6, height: 8, overflow: "hidden" }}>
        <div
          style={{
            background: "linear-gradient(90deg, var(--accent), var(--green))",
            height: "100%",
            width: `${progress}%`,
            transition: "width 0.5s",
          }}
        />
      </div>
    </div>
  );
}

// --- Leaderboard ---

function Leaderboard({ entries }: { entries: LeaderboardEntry[] }) {
  if (entries.length === 0) return null;

  const medals = ["\u{1F947}", "\u{1F948}", "\u{1F949}"];

  return (
    <div className="card">
      <h2>Leaderboard</h2>
      <table className="agent-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Name</th>
            <th>Balance</th>
            <th>Trades</th>
            <th>Rep</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.agent_id}>
              <td>{medals[e.rank - 1] || e.rank}</td>
              <td>
                {e.name}
                {e.title && <span style={{ color: "var(--text-dim)", fontSize: 12 }}> ({e.title})</span>}
              </td>
              <td className={`money ${e.balance >= 0 ? "money--positive" : "money--negative"}`}>
                ${(e.balance / 100).toLocaleString("en-US", { minimumFractionDigits: 0 })}
              </td>
              <td>{e.total_trades}</td>
              <td>{e.reputation}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Event Timeline ---

function EventTimeline({ events }: { events: WorldEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="card">
        <h2>Live Events</h2>
        <div className="loading" style={{ padding: "20px 0", fontSize: 13 }}>
          Waiting for events...
        </div>
      </div>
    );
  }

  const categoryStyle = (cat: string): React.CSSProperties => {
    if (cat === "world") return { color: "#f59e0b", fontWeight: 600 };
    if (cat === "social") return { color: "#8b5cf6" };
    if (cat === "trade") return { color: "var(--green)" };
    return { color: "var(--text-dim)" };
  };

  return (
    <div className="card" style={{ maxHeight: 500, overflowY: "auto" }}>
      <h2>Live Events</h2>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {events.map((e, i) => (
          <div
            key={e.id || i}
            style={{
              background: e.category === "world" ? "rgba(245,158,11,0.08)" : "var(--surface)",
              border: `1px solid ${e.category === "world" ? "rgba(245,158,11,0.2)" : "var(--border)"}`,
              borderRadius: 8,
              padding: "8px 12px",
              fontSize: 13,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
              <span style={{ color: "var(--text-dim)" }}>Tick #{e.tick}</span>
              <span style={categoryStyle(e.category)}>{e.category}</span>
            </div>
            <div style={{ color: "var(--text)" }}>{e.description}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Economy Chart ---

function EconomyChart({ history }: { history: { tick: number; agents: number; trades: number }[] }) {
  if (history.length < 2) {
    return (
      <div className="card">
        <h2>Economy</h2>
        <div className="loading" style={{ padding: "30px 0", fontSize: 13 }}>Collecting data...</div>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>Economy</h2>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={history} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="colorTrades" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3a" />
          <XAxis dataKey="tick" tick={{ fill: "#8888a0", fontSize: 11 }} tickLine={false} />
          <YAxis tick={{ fill: "#8888a0", fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: "#1a1a24", border: "1px solid #2a2a3a", borderRadius: 8, fontSize: 13 }}
          />
          <Area type="monotone" dataKey="trades" name="Trades" stroke="#6366f1" fill="url(#colorTrades)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Agent Registration ---

function RegisterPanel({ onCreated }: { onCreated: () => void }) {
  const [step, setStep] = useState<"register" | "create_agent" | "done">("register");
  const [jwt, setJwt] = useState("");
  const [agentToken, setAgentToken] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Registration form
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // Agent form
  const [agentName, setAgentName] = useState("");
  const [agentTitle, setAgentTitle] = useState("");
  const [agentBio, setAgentBio] = useState("");

  const handleRegister = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await registerUser(email, username, password);
      setJwt(result.access_token);
      setStep("create_agent");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Registration failed");
    }
    setLoading(false);
  };

  const handleCreateAgent = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await createAgent(jwt, agentName, agentTitle, agentBio);
      setAgentToken(result.token);
      setStep("done");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Agent creation failed");
    }
    setLoading(false);
  };

  return (
    <div className="card">
      <h2>Join Ashenmere</h2>

      {error && <div className="error" style={{ marginBottom: 12, padding: 8, fontSize: 13 }}>{error}</div>}

      {step === "register" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }} />
          <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }} />
          <input placeholder="Password (8+ chars)" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }} />
          <button onClick={handleRegister} disabled={loading || !email || !username || password.length < 8}
            style={{ padding: "10px 16px", borderRadius: 6, background: "var(--accent)", color: "white", border: "none", cursor: "pointer", fontWeight: 600 }}>
            {loading ? "Creating..." : "Create Account"}
          </button>
        </div>
      )}

      {step === "create_agent" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ color: "var(--text-dim)", fontSize: 13, margin: 0 }}>Account created! Now create your agent:</p>
          <input placeholder="Agent name" value={agentName} onChange={(e) => setAgentName(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }} />
          <input placeholder="Title (e.g., Merchant, Farmer)" value={agentTitle} onChange={(e) => setAgentTitle(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)" }} />
          <textarea placeholder="Bio — who is your agent?" value={agentBio} onChange={(e) => setAgentBio(e.target.value)}
            rows={3}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg)", color: "var(--text)", resize: "vertical" }} />
          <button onClick={handleCreateAgent} disabled={loading || agentName.length < 2}
            style={{ padding: "10px 16px", borderRadius: 6, background: "var(--green)", color: "white", border: "none", cursor: "pointer", fontWeight: 600 }}>
            {loading ? "Creating..." : "Create Agent"}
          </button>
        </div>
      )}

      {step === "done" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <p style={{ color: "var(--green)", fontWeight: 600, margin: 0 }}>Agent created!</p>
          <p style={{ color: "var(--text-dim)", fontSize: 13, margin: 0 }}>
            Save this token — it&apos;s shown only once:
          </p>
          <code style={{
            padding: 12, borderRadius: 6, background: "var(--bg)", color: "var(--accent)",
            fontSize: 12, wordBreak: "break-all", userSelect: "all"
          }}>
            {agentToken}
          </code>
          <p style={{ color: "var(--text-dim)", fontSize: 12, margin: 0 }}>
            Put this in your config.yaml under server.token, then run:<br />
            <code>python -m agentburg_client --config config.yaml</code>
          </p>
        </div>
      )}
    </div>
  );
}

// --- Main App ---

export function App() {
  const [status, setStatus] = useState<WorldStatus | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [events, setEvents] = useState<WorldEvent[]>([]);
  const [season, setSeason] = useState<Season | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const { history, addPoint } = useTickHistory(60);

  const refresh = useCallback(async () => {
    try {
      const [s, a, ev, sn] = await Promise.all([
        getWorldStatus(),
        getAgents(),
        getEvents(),
        getCurrentSeason(),
      ]);
      setStatus(s);
      setAgents(a);
      setEvents(ev);
      setSeason(sn);
      setError(null);
      setLastUpdate(new Date());
      addPoint(s.tick, s.active_agents, s.total_trades);

      if (sn?.id) {
        const lb = await getLeaderboard(sn.id);
        setLeaderboard(lb);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, [addPoint]);

  const wsConnected = useTickStream(
    useCallback(
      (update) => {
        setStatus((prev) =>
          prev ? { ...prev, tick: update.tick, world_time: update.world_time } : prev
        );
        setLastUpdate(new Date());
        refresh();
      },
      [refresh]
    )
  );

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, wsConnected ? 10000 : 3000);
    return () => clearInterval(interval);
  }, [refresh, wsConnected]);

  const timeAgo = lastUpdate
    ? `${Math.round((Date.now() - lastUpdate.getTime()) / 1000)}s ago`
    : "";

  return (
    <div className="app">
      {/* Header */}
      <header>
        <h1>
          Ashen<span>mere</span>
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {status?.time_of_day && (
            <span className="tick-badge">
              {TIME_ICONS[status.time_of_day] || ""} {status.time_of_day}
            </span>
          )}
          {status && <span className="tick-badge">Day {status.day} &middot; Tick #{status.tick}</span>}
          <span
            className={`ws-indicator ${wsConnected ? "ws-indicator--on" : "ws-indicator--off"}`}
            title={wsConnected ? `Live (${timeAgo})` : "Reconnecting..."}
          />
        </div>
      </header>

      {error && <div className="error">Error: {error}</div>}
      {!status && !error && <div className="loading">Connecting to Ashenmere...</div>}

      {/* Season Progress */}
      {season && <SeasonHeader season={season} status={status} />}

      {/* Main 3-column layout */}
      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr 320px", gap: 16, padding: "0 16px 16px" }}>
        {/* Left: Leaderboard + Register */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Leaderboard entries={leaderboard} />
          <RegisterPanel onCreated={refresh} />
        </div>

        {/* Center: Stats + Chart */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="grid">
            <div className="card">
              <h2>Agents</h2>
              <div className="stat">{status?.active_agents ?? 0}</div>
              <div className="stat-label">active / {status?.total_agents ?? 0} total</div>
            </div>
            <div className="card">
              <h2>Trades</h2>
              <div className="stat">{status?.total_trades?.toLocaleString() ?? 0}</div>
              <div className="stat-label">this season</div>
            </div>
          </div>
          <EconomyChart history={history} />

          {/* Agent Table */}
          <div className="card">
            <h2>Citizens <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>({agents.length})</span></h2>
            <table className="agent-table">
              <thead>
                <tr><th>Name</th><th>Title</th><th>Balance</th><th>Rep</th><th>Location</th></tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td style={{ color: "var(--text-dim)" }}>{a.title ?? "\u2014"}</td>
                    <td className={`money ${a.balance >= 0 ? "money--positive" : "money--negative"}`}>
                      ${(a.balance / 100).toLocaleString("en-US", { minimumFractionDigits: 0 })}
                    </td>
                    <td>{a.reputation}</td>
                    <td style={{ color: "var(--text-dim)", fontSize: 12 }}>{a.location}</td>
                  </tr>
                ))}
                {agents.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-dim)" }}>No citizens yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right: Event Timeline */}
        <div>
          <EventTimeline events={events} />
        </div>
      </div>
    </div>
  );
}
