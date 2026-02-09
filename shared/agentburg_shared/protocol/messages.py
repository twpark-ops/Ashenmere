"""WebSocket message definitions for agent-server communication.

All messages are JSON-serialized Pydantic models with a 'type' discriminator field.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# --- Enums ---


class MessageType(StrEnum):
    """All valid message types in the protocol."""

    # Client → Server
    AUTHENTICATE = "authenticate"
    ACTION = "action"
    QUERY = "query"

    # Server → Client
    TICK_UPDATE = "tick_update"
    OBSERVATION = "observation"
    ACTION_RESULT = "action_result"
    WORLD_EVENT = "world_event"
    SLEEP_SUMMARY = "sleep_summary"
    AUTH_RESULT = "auth_result"
    QUERY_RESULT = "query_result"
    ERROR = "error"


class ActionType(StrEnum):
    """All actions an agent can perform."""

    BUY = "buy"
    SELL = "sell"
    HIRE = "hire"
    FIRE = "fire"
    INVEST = "invest"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    BORROW = "borrow"
    REPAY = "repay"
    BUILD = "build"
    SUE = "sue"
    CHAT = "chat"
    TRADE_OFFER = "trade_offer"
    ACCEPT_OFFER = "accept_offer"
    REJECT_OFFER = "reject_offer"
    START_BUSINESS = "start_business"
    CLOSE_BUSINESS = "close_business"
    SET_PRICE = "set_price"
    IDLE = "idle"


class QueryType(StrEnum):
    """All queries an agent can make."""

    MARKET_PRICES = "market_prices"
    MY_BALANCE = "my_balance"
    MY_INVENTORY = "my_inventory"
    MY_PROPERTIES = "my_properties"
    AGENT_INFO = "agent_info"
    MARKET_ORDERS = "market_orders"
    BANK_RATES = "bank_rates"
    COURT_CASES = "court_cases"
    BUSINESS_LIST = "business_list"
    WORLD_STATUS = "world_status"


# --- Client → Server Messages ---


class AuthenticateMessage(BaseModel):
    """Agent authentication handshake."""

    type: Literal[MessageType.AUTHENTICATE] = MessageType.AUTHENTICATE
    agent_token: str
    client_version: str = "0.1.0"


class ActionMessage(BaseModel):
    """Agent action submission."""

    type: Literal[MessageType.ACTION] = MessageType.ACTION
    action: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: UUID | None = None  # For tracking action results


class QueryMessage(BaseModel):
    """Agent data query."""

    type: Literal[MessageType.QUERY] = MessageType.QUERY
    query: QueryType
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: UUID | None = None


# --- Server → Client Messages ---


class AgentSnapshot(BaseModel):
    """Current state of the agent visible to itself."""

    agent_id: UUID
    name: str
    balance: int  # Integer cents to avoid float precision issues
    inventory: dict[str, int] = Field(default_factory=dict)
    properties: list[UUID] = Field(default_factory=list)
    businesses: list[UUID] = Field(default_factory=list)
    reputation: int = 500  # 0-1000 scale
    credit_score: int = 500  # 0-1000 scale
    location: str = "town_center"
    status: str = "active"


class MarketSnapshot(BaseModel):
    """Current market state visible to the agent."""

    prices: dict[str, int] = Field(default_factory=dict)  # item → price in cents
    volume_24h: dict[str, int] = Field(default_factory=dict)
    trending_up: list[str] = Field(default_factory=list)
    trending_down: list[str] = Field(default_factory=list)


class TickUpdate(BaseModel):
    """World tick update sent to each connected agent."""

    type: Literal[MessageType.TICK_UPDATE] = MessageType.TICK_UPDATE
    tick: int
    world_time: datetime
    agent: AgentSnapshot
    market: MarketSnapshot
    observations: list[str] = Field(default_factory=list)
    pending_offers: list[dict[str, Any]] = Field(default_factory=list)
    pending_cases: list[dict[str, Any]] = Field(default_factory=list)


class ObservationMessage(BaseModel):
    """Mid-tick observation (something happened that affects this agent)."""

    type: Literal[MessageType.OBSERVATION] = MessageType.OBSERVATION
    tick: int
    event: str
    details: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """Result of an agent's action."""

    type: Literal[MessageType.ACTION_RESULT] = MessageType.ACTION_RESULT
    request_id: UUID | None = None
    success: bool
    action: ActionType
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class WorldEvent(BaseModel):
    """Broadcast event affecting the entire world."""

    type: Literal[MessageType.WORLD_EVENT] = MessageType.WORLD_EVENT
    tick: int
    event: str
    severity: str = "info"  # info, warning, critical
    details: dict[str, Any] = Field(default_factory=dict)


class SleepSummary(BaseModel):
    """Summary of what happened while the agent was offline."""

    type: Literal[MessageType.SLEEP_SUMMARY] = MessageType.SLEEP_SUMMARY
    ticks_missed: int
    balance_change: int = 0
    inventory_changes: dict[str, int] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    pending_actions: list[dict[str, Any]] = Field(default_factory=list)


class QueryResult(BaseModel):
    """Result of a query."""

    type: Literal[MessageType.QUERY_RESULT] = MessageType.QUERY_RESULT
    request_id: UUID | None = None
    query: QueryType
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorMessage(BaseModel):
    """Error message from server."""

    type: Literal[MessageType.ERROR] = MessageType.ERROR
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AuthResult(BaseModel):
    """Authentication result."""

    type: Literal[MessageType.AUTH_RESULT] = MessageType.AUTH_RESULT
    success: bool
    agent_id: UUID | None = None
    message: str = ""


# Union type for message routing
ServerMessage = (
    TickUpdate | ObservationMessage | ActionResult | WorldEvent | SleepSummary | QueryResult | ErrorMessage | AuthResult
)

ClientMessage = AuthenticateMessage | ActionMessage | QueryMessage
