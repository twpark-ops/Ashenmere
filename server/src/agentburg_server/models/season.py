"""Season model — time-bounded competitive periods managed by the AI Game Master."""

import enum

from sqlalchemy import Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentburg_server.models.base import Base, TimestampMixin, UUIDMixin


class SeasonStatus(enum.StrEnum):
    PENDING = "pending"   # Created but not started
    ACTIVE = "active"     # Currently running
    ENDED = "ended"       # Finished, leaderboard frozen


class Season(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "seasons"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[SeasonStatus] = mapped_column(
        SAEnum(SeasonStatus, values_callable=lambda x: [e.value for e in x]),
        default=SeasonStatus.PENDING,
        nullable=False,
    )
    theme: Mapped[str] = mapped_column(String(100), default="frontier", nullable=False)
    rules: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    start_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_agents: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    def __repr__(self) -> str:
        return f"<Season {self.name} ({self.status.value})>"
