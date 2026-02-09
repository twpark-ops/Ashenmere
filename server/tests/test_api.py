"""Integration tests for REST API endpoints — registration, login, agents, market."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_and_login(test_client: AsyncClient):
    """Full registration → login flow should return valid tokens."""
    # Register
    reg_resp = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "flow@example.com",
            "username": "flowtest",
            "password": "strongpassword123",
        },
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert reg_data["token_type"] == "bearer"
    assert "access_token" in reg_data
    assert reg_data["user"]["email"] == "flow@example.com"

    # Login with same credentials
    login_resp = await test_client.post(
        "/api/v1/auth/login",
        json={
            "email": "flow@example.com",
            "password": "strongpassword123",
        },
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert "access_token" in login_data


@pytest.mark.anyio
async def test_register_duplicate_email(test_client: AsyncClient):
    """Registering with an existing email should return 409."""
    await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "dup@example.com",
            "username": "user_dup1",
            "password": "password1234",
        },
    )
    resp = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "dup@example.com",
            "username": "user_dup2",
            "password": "password5678",
        },
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_login_wrong_password(test_client: AsyncClient):
    """Login with wrong password should return 401."""
    await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "wrong@example.com",
            "username": "wrongpw",
            "password": "correctpassword",
        },
    )
    resp = await test_client.post(
        "/api/v1/auth/login",
        json={
            "email": "wrong@example.com",
            "password": "incorrectpassword",
        },
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_get_me_authenticated(test_client: AsyncClient):
    """GET /auth/me with valid token should return user profile."""
    reg = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "me@example.com",
            "username": "meuser",
            "password": "mepassword123",
        },
    )
    token = reg.json()["access_token"]

    resp = await test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.anyio
async def test_get_me_unauthenticated(test_client: AsyncClient):
    """GET /auth/me without token should return 401."""
    resp = await test_client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_and_list_agents(test_client: AsyncClient):
    """Create an agent and verify it appears in the list."""
    # Register user
    reg = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "agentcreator@example.com",
            "username": "agentcreator",
            "password": "createpass123",
        },
    )
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create agent
    create_resp = await test_client.post(
        "/api/v1/agents",
        json={
            "name": "TestTrader",
            "title": "Merchant",
            "bio": "A test trading agent",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    agent_data = create_resp.json()
    assert agent_data["agent"]["name"] == "TestTrader"
    assert "token" in agent_data  # Raw API token

    # List all agents
    list_resp = await test_client.get("/api/v1/agents")
    assert list_resp.status_code == 200
    agents = list_resp.json()
    assert any(a["name"] == "TestTrader" for a in agents)

    # Get specific agent
    agent_id = agent_data["agent"]["id"]
    get_resp = await test_client.get(f"/api/v1/agents/{agent_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "TestTrader"


@pytest.mark.anyio
async def test_create_agent_unauthenticated(test_client: AsyncClient):
    """Creating an agent without authentication should fail."""
    resp = await test_client.post(
        "/api/v1/agents",
        json={
            "name": "Unauthorized",
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_get_nonexistent_agent(test_client: AsyncClient):
    """Getting a non-existent agent should return 404."""
    from uuid import uuid4

    resp = await test_client.get(f"/api/v1/agents/{uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# World status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_world_status(test_client: AsyncClient):
    """GET /world/status should return valid world state."""
    resp = await test_client.get("/api/v1/world/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_agents" in data
    assert "active_agents" in data
    assert "total_trades" in data
    assert "tick" in data
    assert "world_time" in data


# ---------------------------------------------------------------------------
# Market endpoints
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_market_prices_empty(test_client: AsyncClient):
    """Market prices should return empty dict when no trades exist."""
    resp = await test_client.get("/api/v1/market/prices")
    assert resp.status_code == 200
    assert "prices" in resp.json()


@pytest.mark.anyio
async def test_market_orders_empty(test_client: AsyncClient):
    """Market orders should return empty list when no orders exist."""
    resp = await test_client.get("/api/v1/market/orders")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_market_trades_empty(test_client: AsyncClient):
    """Market trades should return empty list when no trades exist."""
    resp = await test_client.get("/api/v1/market/trades")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_check(test_client: AsyncClient):
    """GET /health should return ok status."""
    resp = await test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "tick" in data
