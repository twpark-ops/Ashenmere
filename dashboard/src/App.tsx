import { useCallback, useEffect, useRef, useState } from "react";
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
  loginUser,
  registerUser,
} from "./api";
import type { LeaderboardEntry, Season, WorldEvent } from "./api";
import { useTickHistory, useTickStream } from "./hooks";
import type { Agent, WorldStatus } from "./types";

// --- Helpers ---

const TIME_ICONS: Record<string, string> = {
  morning: "\u{1F305}", afternoon: "\u{2600}\u{FE0F}", evening: "\u{1F306}", night: "\u{1F319}",
};

const DEFAULT_SEASON_TICKS = 1008; // 168 days * 6 ticks/day

function formatBalance(cents: number): string {
  return "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 0 });
}

// --- Season Header ---

function SeasonHeader({ season, status }: { season: Season | null; status: WorldStatus | null }) {
  if (!season || !status) return null;
  const startTick = season.start_tick ?? 0;
  const endTick = season.end_tick ?? startTick + DEFAULT_SEASON_TICKS;
  const totalTicks = endTick - startTick;
  const elapsed = Math.max(0, status.tick - startTick);
  const progress = Math.min(100, (elapsed / totalTicks) * 100);
  const daysLeft = Math.max(0, Math.ceil((totalTicks - elapsed) / 6));

  return (
    <div className="card season-header">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{season.name}</h2>
        <span className="dim small">Day {status.day} / {Math.ceil(totalTicks / 6)} &middot; {daysLeft} days left</span>
      </div>
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
    </div>
  );
}

// --- Leaderboard ---

function Leaderboard({ entries }: { entries: LeaderboardEntry[] }) {
  const medals = ["\u{1F947}", "\u{1F948}", "\u{1F949}"];

  return (
    <div className="card">
      <h2>Leaderboard</h2>
      {entries.length === 0 ? (
        <div className="dim small" style={{ padding: "16px 0", textAlign: "center" }}>No rankings yet</div>
      ) : (
        <table className="agent-table">
          <thead>
            <tr><th>#</th><th>Name</th><th>Balance</th><th>Trades</th></tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.agent_id}>
                <td>{medals[e.rank - 1] || e.rank}</td>
                <td>{e.name}{e.title && <span className="dim small"> ({e.title})</span>}</td>
                <td className={`money ${e.balance >= 0 ? "money--positive" : "money--negative"}`}>
                  {formatBalance(e.balance)}
                </td>
                <td>{e.total_trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// --- Event Timeline ---

function EventTimeline({ events }: { events: WorldEvent[] }) {
  return (
    <div className="card event-timeline">
      <h2>Live Events</h2>
      {events.length === 0 ? (
        <div className="dim small" style={{ padding: "20px 0", textAlign: "center" }}>Waiting for events...</div>
      ) : (
        <div className="event-list">
          {events.map((e, i) => (
            <div key={e.id || i} className={`event-item ${e.category === "world" ? "event-item--world" : ""}`}>
              <div className="event-meta">
                <span className="dim">Tick #{e.tick}</span>
                <span className={`event-cat event-cat--${e.category}`}>{e.category}</span>
              </div>
              <div>{e.description}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Economy Chart ---

function EconomyChart({ history }: { history: { tick: number; agents: number; trades: number }[] }) {
  if (history.length < 2) {
    return <div className="card"><h2>Economy</h2><div className="dim small" style={{ padding: 30, textAlign: "center" }}>Collecting data...</div></div>;
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
          <Tooltip contentStyle={{ background: "#1a1a24", border: "1px solid #2a2a3a", borderRadius: 8, fontSize: 13 }} />
          <Area type="monotone" dataKey="trades" name="Trades" stroke="#6366f1" fill="url(#colorTrades)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Agent Registration + Login ---

function AuthPanel({ onCreated }: { onCreated: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const [step, setStep] = useState<"auth" | "create_agent" | "done">("auth");
  const [jwt, setJwt] = useState(() => sessionStorage.getItem("ashenmere_jwt") || "");
  const [agentToken, setAgentToken] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [agentName, setAgentName] = useState("");
  const [agentTitle, setAgentTitle] = useState("");
  const [agentBio, setAgentBio] = useState("");

  // If JWT exists from previous session, go to agent creation
  useEffect(() => {
    if (jwt) setStep("create_agent");
  }, []);

  const handleAuth = async () => {
    setLoading(true);
    setError("");
    try {
      const result = mode === "register"
        ? await registerUser(email, username, password)
        : await loginUser(email, password);
      setJwt(result.access_token);
      sessionStorage.setItem("ashenmere_jwt", result.access_token);
      setStep("create_agent");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Authentication failed");
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

  const handleCopy = () => {
    navigator.clipboard.writeText(agentToken).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleLogout = () => {
    sessionStorage.removeItem("ashenmere_jwt");
    setJwt("");
    setStep("auth");
  };

  return (
    <div className="card">
      <h2>Join Ashenmere</h2>
      {error && <div className="error" style={{ marginBottom: 12, padding: 8, fontSize: 13 }}>{error}</div>}

      {step === "auth" && (
        <div className="form-col">
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <button onClick={() => setMode("register")} className={`tab-btn ${mode === "register" ? "tab-btn--active" : ""}`}>Register</button>
            <button onClick={() => setMode("login")} className={`tab-btn ${mode === "login" ? "tab-btn--active" : ""}`}>Login</button>
          </div>
          <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} className="form-input" />
          {mode === "register" && (
            <input placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} className="form-input" />
          )}
          <input placeholder="Password (8+ chars)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="form-input" />
          <button onClick={handleAuth} disabled={loading || !email || password.length < 8} className="form-btn form-btn--primary">
            {loading ? "..." : mode === "register" ? "Create Account" : "Login"}
          </button>
        </div>
      )}

      {step === "create_agent" && (
        <div className="form-col">
          <p className="dim small" style={{ margin: 0 }}>Create your agent for this season:</p>
          <input placeholder="Agent name" value={agentName} onChange={(e) => setAgentName(e.target.value)} className="form-input" />
          <input placeholder="Title (e.g., Merchant)" value={agentTitle} onChange={(e) => setAgentTitle(e.target.value)} className="form-input" />
          <textarea placeholder="Bio — who is your agent?" value={agentBio} onChange={(e) => setAgentBio(e.target.value)} rows={3} className="form-input" style={{ resize: "vertical" }} />
          <button onClick={handleCreateAgent} disabled={loading || agentName.length < 2} className="form-btn form-btn--green">
            {loading ? "..." : "Create Agent"}
          </button>
          <button onClick={handleLogout} className="form-btn dim small" style={{ background: "transparent", border: "none", textDecoration: "underline", cursor: "pointer" }}>Logout</button>
        </div>
      )}

      {step === "done" && (
        <div className="form-col">
          <p style={{ color: "var(--green)", fontWeight: 600, margin: 0 }}>Agent created!</p>
          <p className="dim small" style={{ margin: 0 }}>Save this token — shown only once:</p>
          <code className="token-display">{agentToken}</code>
          <button onClick={handleCopy} className="form-btn form-btn--primary">
            {copied ? "Copied!" : "Copy Token"}
          </button>
          <p className="dim" style={{ fontSize: 11, margin: 0 }}>
            Add to config.yaml → server.token, then:<br />
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
  const { history, addPoint } = useTickHistory(60);
  const lastRefresh = useRef(0);

  const refresh = useCallback(async () => {
    // Throttle: at most once per 3 seconds
    const now = Date.now();
    if (now - lastRefresh.current < 3000) return;
    lastRefresh.current = now;

    try {
      const [s, a, ev, sn] = await Promise.all([
        getWorldStatus(), getAgents(), getEvents(), getCurrentSeason(),
      ]);
      setStatus(s);
      setAgents(a);
      setEvents(ev);
      setSeason(sn);
      setError(null);
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
    useCallback((update) => {
      setStatus((prev) => prev ? { ...prev, tick: update.tick, world_time: update.world_time } : prev);
      refresh();
    }, [refresh])
  );

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, wsConnected ? 10000 : 3000);
    return () => clearInterval(interval);
  }, [refresh, wsConnected]);

  return (
    <div className="app">
      <header>
        <h1>Ashen<span>mere</span></h1>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {status?.time_of_day && (
            <span className="tick-badge">{TIME_ICONS[status.time_of_day] || ""} {status.time_of_day}</span>
          )}
          {status && <span className="tick-badge">Day {status.day} &middot; Tick #{status.tick}</span>}
          <span className={`ws-indicator ${wsConnected ? "ws-indicator--on" : "ws-indicator--off"}`}
            title={wsConnected ? "Live updates" : "Reconnecting..."} />
        </div>
      </header>

      {error && <div className="error">Error: {error}</div>}
      {!status && !error && <div className="loading">Connecting to Ashenmere...</div>}

      {season && <SeasonHeader season={season} status={status} />}

      <div className="main-grid">
        <div className="col-left">
          <Leaderboard entries={leaderboard} />
          <AuthPanel onCreated={refresh} />
        </div>

        <div className="col-center">
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
          <div className="card">
            <h2>Citizens <span className="dim" style={{ fontWeight: 400 }}>({agents.length})</span></h2>
            <table className="agent-table">
              <thead><tr><th>Name</th><th>Title</th><th>Balance</th><th>Rep</th><th>Location</th></tr></thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td className="dim">{a.title ?? "\u2014"}</td>
                    <td className={`money ${a.balance >= 0 ? "money--positive" : "money--negative"}`}>{formatBalance(a.balance)}</td>
                    <td>{a.reputation}</td>
                    <td className="dim small">{a.location}</td>
                  </tr>
                ))}
                {agents.length === 0 && (
                  <tr><td colSpan={5} style={{ textAlign: "center" }} className="dim">No citizens yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="col-right">
          <EventTimeline events={events} />
        </div>
      </div>
    </div>
  );
}
