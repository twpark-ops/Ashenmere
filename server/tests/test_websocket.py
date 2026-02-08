"""Integration tests for the WebSocket endpoint — auth, actions, queries."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent_with_token(
    session: AsyncSession,
    *,
    name: str = "WSAgent",
    balance: int = 10_000,
) -> tuple[Agent, str]:
    """Create an agent and return (agent, raw_token) for WebSocket auth."""
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
    session.add(agent)
    await session.flush()
    await session.commit()
    return agent, raw_token


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_auth_success(db_session: AsyncSession, test_client: AsyncClient):
    """Successful WebSocket authentication should return agent_id."""
    from starlette.testclient import TestClient

    from agentburg_server.db import get_session
    from agentburg_server.main import app

    agent, raw_token = await _make_agent_with_token(db_session)

    # Use Starlette's sync TestClient for WebSocket testing
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "authenticate",
            "agent_token": raw_token,
        })
        response = ws.receive_json()

        assert response["success"] is True
        assert response["agent_id"] == str(agent.id)
        assert "Welcome" in response.get("message", "")


@pytest.mark.anyio
async def test_ws_auth_invalid_token(db_session: AsyncSession, test_client: AsyncClient):
    """Authentication with an invalid token should fail."""
    from starlette.testclient import TestClient
    from agentburg_server.main import app

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "authenticate",
            "agent_token": "invalid_token_12345",
        })
        response = ws.receive_json()

        assert response["success"] is False
        assert "Invalid" in response.get("message", "")


@pytest.mark.anyio
async def test_ws_auth_required_first(db_session: AsyncSession, test_client: AsyncClient):
    """Sending a non-auth message first should be rejected."""
    from starlette.testclient import TestClient
    from agentburg_server.main import app

    client = TestClient(app)
    with pytest.raises(Exception):
        # Server should close the connection after non-auth first message
        with client.websocket_connect("/ws") as ws:
            ws.send_json({
                "type": "action",
                "action": "idle",
                "params": {},
            })
            response = ws.receive_json()
            assert response.get("code") == "AUTH_REQUIRED"


# ---------------------------------------------------------------------------
# Action and query tests (require auth first)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ws_query_balance(db_session: AsyncSession, test_client: AsyncClient):
    """After auth, querying balance should return the agent's financial info."""
    from starlette.testclient import TestClient
    from agentburg_server.main import app

    agent, raw_token = await _make_agent_with_token(db_session, balance=15_000)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Authenticate
        ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = ws.receive_json()
        assert auth_resp["success"] is True

        # Send query
        ws.send_json({
            "type": "query",
            "query": "my_balance",
            "params": {},
        })
        query_resp = ws.receive_json()

        assert query_resp.get("type") == "query_result"
        assert query_resp.get("query") == "my_balance"


@pytest.mark.anyio
async def test_ws_action_idle(db_session: AsyncSession, test_client: AsyncClient):
    """The idle action should succeed without side effects."""
    from starlette.testclient import TestClient
    from agentburg_server.main import app

    agent, raw_token = await _make_agent_with_token(db_session)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Authenticate
        ws.send_json({"type": "authenticate", "agent_token": raw_token})
        auth_resp = ws.receive_json()
        assert auth_resp["success"] is True

        # Send idle action
        ws.send_json({
            "type": "action",
            "action": "idle",
            "params": {},
        })
        action_resp = ws.receive_json()

        assert action_resp.get("type") == "action_result"
        assert action_resp.get("success") is True


@pytest.mark.anyio
async def test_ws_unknown_message_type(db_session: AsyncSession, test_client: AsyncClient):
    """Unknown message types should return an error without crashing."""
    from starlette.testclient import TestClient
    from agentburg_server.main import app

    agent, raw_token = await _make_agent_with_token(db_session)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "authenticate", "agent_token": raw_token})
        ws.receive_json()  # auth response

        ws.send_json({"type": "bogus_type", "data": "test"})
        resp = ws.receive_json()

        assert resp.get("type") == "error"
        assert resp.get("code") == "UNKNOWN_MESSAGE"
