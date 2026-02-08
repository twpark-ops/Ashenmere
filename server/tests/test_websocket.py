"""Integration tests for the WebSocket endpoint — auth, actions, queries.

Uses a lightweight ASGI WebSocket client to test the WebSocket handlers
directly without requiring external libraries or sync/async conflicts.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier

# ---------------------------------------------------------------------------
# Lightweight ASGI WebSocket client
# ---------------------------------------------------------------------------


class _ASGIWebSocket:
    """Minimal async WebSocket client that speaks ASGI protocol directly."""

    def __init__(self, app, path: str = "/ws"):
        self._app = app
        self._path = path
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._receive_queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        # Queue the initial connect event
        await self._send_queue.put({"type": "websocket.connect"})

        # Start the ASGI app in a background task
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "path": self._path,
            "query_string": b"",
            "root_path": "",
            "scheme": "ws",
            "headers": [],
            "subprotocols": [],
        }
        self._task = asyncio.create_task(
            self._app(scope, self._send_queue.get, self._receive_queue.put)
        )

        # Wait for the accept message
        msg = await self._receive_queue.get()
        assert msg["type"] == "websocket.accept", f"Expected accept, got {msg['type']}"
        return self

    async def __aexit__(self, *args):
        # Send disconnect
        await self._send_queue.put({"type": "websocket.disconnect", "code": 1000})
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (TimeoutError, Exception):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def send_json(self, data: dict) -> None:
        import json
        await self._send_queue.put({
            "type": "websocket.receive",
            "text": json.dumps(data),
        })

    async def receive_json(self, timeout: float = 5.0) -> dict:
        import json
        msg = await asyncio.wait_for(self._receive_queue.get(), timeout=timeout)
        if msg["type"] == "websocket.send":
            return json.loads(msg["text"])
        if msg["type"] == "websocket.close":
            raise ConnectionError(f"WebSocket closed with code {msg.get('code')}")
        raise ValueError(f"Unexpected ASGI message type: {msg['type']}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent_with_token(
    factory: async_sessionmaker[AsyncSession],
    *,
    name: str = "WSAgent",
    balance: int = 10_000,
) -> tuple[Agent, str]:
    """Create an agent in a fresh session and return (agent, raw_token)."""
    raw_token = f"ab_test_{uuid4().hex[:16]}"
    token_hash = sha256(raw_token.encode()).hexdigest()

    agent = Agent(
        id=uuid4(),
        name=name,
        api_token_hash=token_hash,
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory={"wheat": 50, "bread": 20},
        location="downtown",
        reputation=500,
        credit_score=700,
    )
    async with factory() as session:
        session.add(agent)
        await session.commit()
    return agent, raw_token


@pytest.fixture
def ws_setup(db_engine):
    """Set up the WS test environment: factory override + app reference."""
    import agentburg_server.db as db_module
    from agentburg_server.main import app

    test_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    original = db_module.get_session_factory
    db_module.get_session_factory = lambda: test_factory

    yield app, test_factory

    db_module.get_session_factory = original


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_auth_success(ws_setup):
    """Successful WebSocket authentication should return agent_id."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({
            "type": "authenticate",
            "agent_token": raw_token,
        })
        response = await ws.receive_json()

        assert response["success"] is True
        assert response["agent_id"] == str(agent.id)
        assert "Welcome" in response.get("message", "")


@pytest.mark.anyio
async def test_ws_auth_invalid_token(ws_setup):
    """Authentication with an invalid token should fail."""
    app, factory = ws_setup

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({
            "type": "authenticate",
            "agent_token": "invalid_token_12345",
        })
        response = await ws.receive_json()

        assert response["success"] is False
        assert "Invalid" in response.get("message", "")


@pytest.mark.anyio
async def test_ws_auth_required_first(ws_setup):
    """Sending a non-auth message first should be rejected."""
    app, factory = ws_setup

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({
            "type": "action",
            "action": "idle",
            "params": {},
        })
        response = await ws.receive_json()
        assert response.get("code") == "AUTH_REQUIRED"


# ---------------------------------------------------------------------------
# Action and query tests (require auth first)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_query_balance(ws_setup):
    """After auth, querying balance should return the agent's financial info."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=15_000)

    async with _ASGIWebSocket(app) as ws:
        # Authenticate
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = await ws.receive_json()
        assert auth_resp["success"] is True

        # Send query
        await ws.send_json({
            "type": "query",
            "query": "my_balance",
            "params": {},
        })
        query_resp = await ws.receive_json()

        assert query_resp.get("type") == "query_result"
        assert query_resp.get("query") == "my_balance"


@pytest.mark.anyio
async def test_ws_action_idle(ws_setup):
    """The idle action should succeed without side effects."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        # Authenticate
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = await ws.receive_json()
        assert auth_resp["success"] is True

        # Send idle action
        await ws.send_json({
            "type": "action",
            "action": "idle",
            "params": {},
        })
        action_resp = await ws.receive_json()

        assert action_resp.get("type") == "action_result"
        assert action_resp.get("success") is True


@pytest.mark.anyio
async def test_ws_unknown_message_type(ws_setup):
    """Unknown message types should return an error without crashing."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()  # auth response

        await ws.send_json({"type": "bogus_type", "data": "test"})
        resp = await ws.receive_json()

        assert resp.get("type") == "error"
        assert resp.get("code") == "UNKNOWN_MESSAGE"


# ---------------------------------------------------------------------------
# Extended action tests (cover action_handler branches)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_action_buy(ws_setup):
    """A buy action should create a market order and reserve funds."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=50_000)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = await ws.receive_json()
        assert auth_resp["success"] is True

        await ws.send_json({
            "type": "action",
            "action": "buy",
            "params": {"item": "wheat", "price": 100, "quantity": 5},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is True
        assert "order_id" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_action_sell(ws_setup):
    """A sell action should create a sell order and reserve inventory."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = await ws.receive_json()
        assert auth_resp["success"] is True

        await ws.send_json({
            "type": "action",
            "action": "sell",
            "params": {"item": "wheat", "price": 150, "quantity": 10},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is True
        assert "order_id" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_action_buy_missing_params(ws_setup):
    """A buy action without item or price should fail gracefully."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "action",
            "action": "buy",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is False
        assert "Missing" in resp.get("message", "")


@pytest.mark.anyio
async def test_ws_action_chat(ws_setup):
    """A chat action should succeed and return an event_id."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "action",
            "action": "chat",
            "params": {"message": "Hello world!"},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is True
        assert "event_id" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_action_chat_empty(ws_setup):
    """An empty chat message should be rejected."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "action",
            "action": "chat",
            "params": {"message": ""},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is False


@pytest.mark.anyio
async def test_ws_action_start_business(ws_setup):
    """Starting a business via WebSocket should succeed."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=100_000)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "action",
            "action": "start_business",
            "params": {
                "name": "Test Shop",
                "business_type": "shop",
                "location": "downtown",
            },
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is True
        assert "business_id" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_action_build(ws_setup):
    """Building a property via WebSocket should succeed."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=500_000)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "action",
            "action": "build",
            "params": {
                "name": "Test Building",
                "property_type": "building",
                "location": "uptown",
            },
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "action_result"
        assert resp.get("success") is True
        assert "property_id" in resp.get("data", {})


# ---------------------------------------------------------------------------
# Extended query tests (cover query_handler branches)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_query_inventory(ws_setup):
    """Querying inventory should return the agent's items."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = await ws.receive_json()
        assert auth_resp["success"] is True

        await ws.send_json({
            "type": "query",
            "query": "my_inventory",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "my_inventory"
        data = resp.get("data", {})
        assert "inventory" in data
        assert data["inventory"].get("wheat") == 50


@pytest.mark.anyio
async def test_ws_query_world_status(ws_setup):
    """Querying world status should return aggregate stats."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "world_status",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "world_status"
        data = resp.get("data", {})
        assert "total_agents" in data
        assert "active_agents" in data
        assert data["total_agents"] >= 1


@pytest.mark.anyio
async def test_ws_query_market_prices(ws_setup):
    """Querying market prices should return price data."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "market_prices",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "market_prices"
        assert "prices" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_query_bank_rates(ws_setup):
    """Querying bank rates should return interest rate data."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "bank_rates",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "bank_rates"
        data = resp.get("data", {})
        assert "savings_rate" in data
        assert "loan_base_rate" in data


@pytest.mark.anyio
async def test_ws_query_business_list(ws_setup):
    """Querying business list should return businesses."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "business_list",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "business_list"
        assert "businesses" in resp.get("data", {})


@pytest.mark.anyio
async def test_ws_query_market_orders(ws_setup):
    """Querying market orders should return open orders."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=50_000)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        # Place an order first to have something to query
        await ws.send_json({
            "type": "action",
            "action": "buy",
            "params": {"item": "wheat", "price": 100, "quantity": 5},
        })
        await ws.receive_json()

        # Query open orders
        await ws.send_json({
            "type": "query",
            "query": "market_orders",
            "params": {"item": "wheat"},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "market_orders"
        orders = resp.get("data", {}).get("orders", [])
        assert len(orders) >= 1
        assert orders[0]["item"] == "wheat"


@pytest.mark.anyio
async def test_ws_query_balance_data(ws_setup):
    """Querying balance should return correct balance amount."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory, balance=25_000)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "my_balance",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        data = resp.get("data", {})
        assert data["balance"] == 25_000


@pytest.mark.anyio
async def test_ws_query_court_cases(ws_setup):
    """Querying court cases should return cases for the agent."""
    app, factory = ws_setup
    agent, raw_token = await _make_agent_with_token(factory)

    async with _ASGIWebSocket(app) as ws:
        await ws.send_json({"type": "authenticate", "agent_token": raw_token})
        await ws.receive_json()

        await ws.send_json({
            "type": "query",
            "query": "court_cases",
            "params": {},
        })
        resp = await ws.receive_json()

        assert resp.get("type") == "query_result"
        assert resp.get("query") == "court_cases"
        assert "cases" in resp.get("data", {})
