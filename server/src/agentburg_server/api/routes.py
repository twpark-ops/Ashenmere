"""REST API routes for user management, agent registration, and world queries."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.db import get_session
from agentburg_server.models.user import User
from agentburg_server.models.agent import Agent

router = APIRouter(tags=["api"])


# --- Schemas ---


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    username: str
    is_active: bool
    max_agents: int

    model_config = {"from_attributes": True}


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


class WorldStatusResponse(BaseModel):
    total_agents: int
    active_agents: int
    total_trades: int
    tick: int


# --- Routes ---


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    offset: int = 0,
) -> list[Agent]:
    """List all agents in the world."""
    stmt = select(Agent).offset(offset).limit(limit)
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


@router.get("/world/status", response_model=WorldStatusResponse)
async def world_status(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get current world status."""
    from sqlalchemy import func

    from agentburg_server.models.agent import AgentStatus
    from agentburg_server.models.economy import Trade

    total = await session.scalar(select(func.count()).select_from(Agent))
    active = await session.scalar(
        select(func.count()).select_from(Agent).where(Agent.status == AgentStatus.ACTIVE)
    )
    trades = await session.scalar(select(func.count()).select_from(Trade))

    return {
        "total_agents": total or 0,
        "active_agents": active or 0,
        "total_trades": trades or 0,
        "tick": 0,  # TODO: get from tick engine
    }
