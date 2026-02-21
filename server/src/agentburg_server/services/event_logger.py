"""Shared event logging helper for the world event ledger."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.event import EventCategory, WorldEventLog


async def log_event(
    session: AsyncSession,
    tick: int,
    category: EventCategory,
    event_type: str,
    description: str,
    agent_id: UUID | None = None,
    target_id: UUID | None = None,
    data: dict | None = None,
) -> None:
    """Create an immutable event log entry in the world ledger."""
    event = WorldEventLog(
        tick=tick,
        category=category,
        event_type=event_type,
        agent_id=agent_id,
        target_id=target_id,
        description=description,
        data=data or {},
    )
    session.add(event)
