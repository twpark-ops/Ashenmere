"""REST API routes for user management, agent registration, and world queries."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.api.deps import get_current_user
from agentburg_server.db import get_session
from agentburg_server.engine.tick import tick_engine
from agentburg_server.models.agent import Agent, AgentStatus
from agentburg_server.models.economy import MarketOrder, OrderStatus, Trade
from agentburg_server.models.user import User
from agentburg_server.services.auth import create_agent, login_user, register_user

router = APIRouter(tags=["api"])

MAX_LIST_LIMIT = 100


# --- Request / Response Schemas ---


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    email: str
    username: str
    is_active: bool
    max_agents: int

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    title: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=500)


class AgentResponse(BaseModel):
    id: UUID
    name: str
    title: str | None
    tier: str
    status: str
    balance: int
    reputation: int
    location: str

    model_config = {"from_attributes": True}


class AgentCreateResponse(BaseModel):
    agent: AgentResponse
    token: str  # Raw API token — shown only once


class WorldStatusResponse(BaseModel):
    total_agents: int
    active_agents: int
    total_trades: int
    tick: int
    world_time: str


# --- Auth Routes ---


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Register a new user account."""
    try:
        user = await register_user(session, body.email, body.username, body.password)
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    from agentburg_server.services.auth import create_access_token

    token = create_access_token(user.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Log in with email and password."""
    try:
        user, token = await login_user(session, body.email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@router.get("/auth/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)) -> User:
    """Get the current user's profile."""
    return user


# --- Agent Routes ---


@router.post("/agents", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_endpoint(
    body: AgentCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a new agent for the authenticated user."""
    try:
        agent, raw_token = await create_agent(
            session, user, body.name, body.title, body.bio
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return {"agent": agent, "token": raw_token}


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[Agent]:
    """List all agents in the world."""
    stmt = select(Agent).offset(offset).limit(min(limit, MAX_LIST_LIMIT))
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Agent:
    """Get agent details."""
    stmt = select(Agent).where(Agent.id == agent_id)
    result = await session.execute(stmt)
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.get("/my/agents", response_model=list[AgentResponse])
async def list_my_agents(
    user: User = Depends(get_current_user),
) -> list[Agent]:
    """List the current user's agents."""
    return user.agents or []


# --- World Routes ---


@router.get("/world/status", response_model=WorldStatusResponse)
async def world_status(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get current world status."""
    total = await session.scalar(select(func.count()).select_from(Agent))
    active = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ACTIVE)
    )
    trades = await session.scalar(select(func.count()).select_from(Trade))

    return {
        "total_agents": total or 0,
        "active_agents": active or 0,
        "total_trades": trades or 0,
        "tick": tick_engine.tick,
        "world_time": str(tick_engine.world_time),
    }


# --- Market Routes ---


class TradeResponse(BaseModel):
    id: UUID
    tick: int
    item: str
    buyer_id: UUID
    seller_id: UUID
    price: int
    quantity: int
    total: int

    model_config = {"from_attributes": True}


class OrderResponse(BaseModel):
    id: UUID
    agent_id: UUID
    item: str
    side: str
    price: int
    quantity: int
    filled_quantity: int
    status: str
    tick_created: int

    model_config = {"from_attributes": True}


class MarketPricesResponse(BaseModel):
    prices: dict[str, int]


@router.get("/market/prices", response_model=MarketPricesResponse)
async def market_prices(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get latest VWAP prices for all traded items."""
    from agentburg_server.services.market import get_market_prices

    prices = await get_market_prices(session)
    return {"prices": prices}


@router.get("/market/orders", response_model=list[OrderResponse])
async def list_orders(
    session: AsyncSession = Depends(get_session),
    item: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
) -> list:
    """List open market orders, optionally filtered by item."""
    stmt = select(MarketOrder).where(MarketOrder.status == OrderStatus.OPEN)
    if item:
        stmt = stmt.where(MarketOrder.item == item)
    stmt = stmt.order_by(MarketOrder.tick_created.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/market/trades", response_model=list[TradeResponse])
async def list_trades(
    session: AsyncSession = Depends(get_session),
    item: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT),
) -> list:
    """List recent trades, optionally filtered by item."""
    stmt = select(Trade)
    if item:
        stmt = stmt.where(Trade.item == item)
    stmt = stmt.order_by(Trade.tick.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
