"""User model — registered users who own agents."""

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentburg_server.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_agents: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Relationships
    agents: Mapped[list["Agent"]] = relationship(back_populates="owner", lazy="selectin")  # noqa: F821

    def __repr__(self) -> str:
        return f"<User {self.username}>"
