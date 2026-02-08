"""Prometheus metrics for AgentBurg server."""

from prometheus_client import Counter, Gauge, Histogram, Info

# Server info
server_info = Info("agentburg", "AgentBurg server information")
server_info.info({"version": "0.1.0"})

# Tick engine
tick_current = Gauge("agentburg_tick_current", "Current world tick number")
tick_duration_seconds = Histogram(
    "agentburg_tick_duration_seconds",
    "Time spent processing a world tick",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Agents
agents_connected = Gauge("agentburg_agents_connected", "Currently connected agents")
agents_total = Gauge("agentburg_agents_total", "Total registered agents")

# WebSocket
ws_messages_received = Counter(
    "agentburg_ws_messages_received_total",
    "Total WebSocket messages received",
    ["message_type"],
)
ws_messages_sent = Counter(
    "agentburg_ws_messages_sent_total",
    "Total WebSocket messages sent",
    ["message_type"],
)
ws_connections_total = Counter(
    "agentburg_ws_connections_total",
    "Total WebSocket connections established",
)
ws_auth_failures = Counter(
    "agentburg_ws_auth_failures_total",
    "Total WebSocket authentication failures",
)

# Actions
action_requests = Counter(
    "agentburg_action_requests_total",
    "Total action requests by type",
    ["action_type"],
)
action_errors = Counter(
    "agentburg_action_errors_total",
    "Total action errors by type",
    ["action_type"],
)
action_duration_seconds = Histogram(
    "agentburg_action_duration_seconds",
    "Time spent handling an action",
    ["action_type"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)

# Economy
trades_total = Counter("agentburg_trades_total", "Total executed trades")
trade_volume = Counter("agentburg_trade_volume_cents_total", "Total trade volume in cents")
orders_placed = Counter(
    "agentburg_orders_placed_total",
    "Total market orders placed",
    ["side"],
)
loans_issued = Counter("agentburg_loans_issued_total", "Total loans issued")
court_cases_filed = Counter("agentburg_court_cases_filed_total", "Total court cases filed")
court_verdicts = Counter(
    "agentburg_court_verdicts_total",
    "Total court verdicts",
    ["result"],
)

# HTTP
http_requests = Counter(
    "agentburg_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
http_duration_seconds = Histogram(
    "agentburg_http_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
