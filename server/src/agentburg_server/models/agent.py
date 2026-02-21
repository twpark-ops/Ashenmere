"""Agent model — AI agents living in the world."""

import enum
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentburg_server.models.base import Base, TimestampMixin, UUIDMixin


class AgentStatus(enum.StrEnum):
    ACTIVE = "active"
    SLEEPING = "sleeping"  # Owner offline
    BANKRUPT = "bankrupt"
    JAILED = "jailed"
    SUSPENDED = "suspended"


class AgentTier(enum.StrEnum):
    PLAYER = "player"  # User-controlled via Docker client
    NPC_LLM = "npc_llm"  # Server-hosted LLM agent
    NPC_RULE = "npc_rule"  # Server-hosted rule-based agent


class Agent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agents"

    # Identity
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(100))  # e.g., "Merchant", "Farmer"
    bio: Mapped[str | None] = mapped_column(Text)

    # Owner (NULL for NPC agents)
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    owner: Mapped["User | None"] = relationship(back_populates="agents")  # noqa: F821

    # Authentication
    api_token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Classification
    tier: Mapped[AgentTier] = mapped_column(SAEnum(AgentTier, values_callable=lambda x: [e.value for e in x]), default=AgentTier.PLAYER, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        SAEnum(AgentStatus, values_callable=lambda x: [e.value for e in x]), default=AgentStatus.ACTIVE, nullable=False, index=True
    )

    # Economics
    balance: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)  # In cents
    inventory: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    location: Mapped[str] = mapped_column(String(100), default="town_center", nullable=False)

    # Reputation
    reputation: Mapped[int] = mapped_column(Integer, default=500, nullable=False)  # 0-1000
    credit_score: Mapped[int] = mapped_column(Integer, default=500, nullable=False)  # 0-1000

    # Stats
    total_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_earnings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lawsuits_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lawsuits_lost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Connection state
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_tick: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    accounts: Mapped[list["Account"]] = relationship(back_populates="agent", lazy="selectin")  # noqa: F821
    properties: Mapped[list["Property"]] = relationship(back_populates="owner", lazy="selectin")  # noqa: F821
    businesses: Mapped[list["Business"]] = relationship(back_populates="owner", lazy="selectin")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.tier.value}) balance={self.balance}>"
