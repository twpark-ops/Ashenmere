"""Event log model — immutable audit trail for all world events."""

from datetime import datetime
from uuid import UUID
import enum

from sqlalchemy import String, Integer, DateTime, Text, Enum as SAEnum, Index, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentburg_server.models.base import Base, UUIDMixin


class EventCategory(str, enum.Enum):
    TRADE = "trade"
    BANK = "bank"
    PROPERTY = "property"
    COURT = "court"
    CONTRACT = "contract"
    BUSINESS = "business"
    SOCIAL = "social"
    SYSTEM = "system"
    CRIME = "crime"


class WorldEventLog(Base, UUIDMixin):
    """Immutable event log for auditing and replay. Event sourcing backbone."""

    __tablename__ = "world_events"
    __table_args__ = (
        Index("ix_events_tick_cat", "tick", "category"),
        Index("ix_events_agent", "agent_id"),
    )

    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[EventCategory] = mapped_column(SAEnum(EventCategory), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
