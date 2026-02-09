"""Economic models — accounts, orders, trades, properties."""

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentburg_server.models.base import Base, TimestampMixin, UUIDMixin

# --- Bank Account ---


class AccountType(enum.StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    LOAN = "loan"


class Account(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance >= 0 OR account_type IN ('loan', 'LOAN')", name="ck_account_balance"),)

    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    agent: Mapped["Agent"] = relationship(back_populates="accounts")  # noqa: F821

    account_type: Mapped[AccountType] = mapped_column(SAEnum(AccountType), default=AccountType.CHECKING, nullable=False)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interest_rate: Mapped[int] = mapped_column(Integer, default=300, nullable=False)  # basis points (300 = 3%)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --- Market Orders ---


class OrderSide(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(enum.StrEnum):
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MarketOrder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "market_orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_quantity_positive"),
        CheckConstraint("price > 0", name="ck_order_price_positive"),
        Index("ix_orders_matching", "item", "side", "status", "price"),
    )

    agent_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False, index=True)
    item: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[OrderSide] = mapped_column(SAEnum(OrderSide), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # In cents
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.OPEN, nullable=False)
    tick_created: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_expires: Mapped[int | None] = mapped_column(Integer)  # NULL = no expiry


# --- Trades (Immutable Event Log) ---


class Trade(Base, UUIDMixin):
    """Immutable record of a completed trade. Event sourcing pattern."""

    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_tick", "tick"),
        Index("ix_trades_item", "item"),
    )

    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    item: Mapped[str] = mapped_column(String(100), nullable=False)
    buyer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    seller_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)  # price * quantity
    buy_order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("market_orders.id"), nullable=False)
    sell_order_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("market_orders.id"), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --- Property Registry ---


class PropertyType(enum.StrEnum):
    LAND = "land"
    BUILDING = "building"
    SHOP = "shop"
    FACTORY = "factory"
    HOUSE = "house"


class Property(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "properties"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    property_type: Mapped[PropertyType] = mapped_column(SAEnum(PropertyType), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id"), nullable=True, index=True
    )
    owner: Mapped["Agent | None"] = relationship(back_populates="properties")  # noqa: F821
    market_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_for_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    asking_price: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
