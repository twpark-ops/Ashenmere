"""Plugin base class and hook type definitions."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class HookType(enum.StrEnum):
    """All available hook points in the AgentBurg server."""

    # Server lifecycle
    ON_STARTUP = "on_startup"
    ON_SHUTDOWN = "on_shutdown"

    # Tick engine
    BEFORE_TICK = "before_tick"
    AFTER_TICK = "after_tick"

    # Action handling
    BEFORE_ACTION = "before_action"
    AFTER_ACTION = "after_action"

    # Agent connections
    ON_AGENT_CONNECT = "on_agent_connect"
    ON_AGENT_DISCONNECT = "on_agent_disconnect"

    # Economic events
    ON_TRADE = "on_trade"
    ON_VERDICT = "on_verdict"


@dataclass(frozen=True)
class PluginMetadata:
    """Immutable metadata describing a plugin."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    priority: int = 100  # Lower = runs first (0-999)


class Plugin:
    """Base class for AgentBurg plugins.

    Subclass this and override the hook methods you need.
    All hook methods are async and receive contextual keyword arguments.
    """

    metadata: PluginMetadata = PluginMetadata(name="unnamed")

    # -- Server lifecycle ----------------------------------------------------

    async def on_startup(self) -> None:
        """Called when the server starts up."""

    async def on_shutdown(self) -> None:
        """Called when the server shuts down."""

    # -- Tick engine ---------------------------------------------------------

    async def before_tick(self, *, tick: int) -> None:
        """Called before each world tick is processed."""

    async def after_tick(
        self,
        *,
        tick: int,
        trades: int,
        verdicts: int,
        payments: int,
        interest: int,
        elapsed: float,
    ) -> None:
        """Called after each world tick completes."""

    # -- Action handling -----------------------------------------------------

    async def before_action(
        self,
        *,
        agent_id: UUID,
        action: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Called before an action is dispatched.

        Return a dict to override params, or None to keep original.
        Raise ValueError to block the action with an error message.
        """
        return None

    async def after_action(
        self,
        *,
        agent_id: UUID,
        action: str,
        success: bool,
        data: dict[str, Any],
    ) -> None:
        """Called after an action completes (success or failure)."""

    # -- Agent connections ---------------------------------------------------

    async def on_agent_connect(self, *, agent_id: UUID) -> None:
        """Called when an agent connects via WebSocket."""

    async def on_agent_disconnect(self, *, agent_id: UUID) -> None:
        """Called when an agent disconnects."""

    # -- Economic events -----------------------------------------------------

    async def on_trade(
        self,
        *,
        session: AsyncSession,
        tick: int,
        buyer_id: UUID,
        seller_id: UUID,
        item: str,
        price: int,
        quantity: int,
    ) -> None:
        """Called when a trade is executed during batch auction."""

    async def on_verdict(
        self,
        *,
        session: AsyncSession,
        tick: int,
        case_id: UUID,
        plaintiff_id: UUID,
        defendant_id: UUID,
        guilty: bool,
        fine: int,
    ) -> None:
        """Called when a court case verdict is delivered."""

    # -- Utility -------------------------------------------------------------

    @property
    def name(self) -> str:
        """Shortcut for plugin name."""
        return self.metadata.name

    def __repr__(self) -> str:
        return f"<Plugin {self.metadata.name} v{self.metadata.version}>"


@dataclass
class HookContext:
    """Context object passed alongside hook kwargs for advanced use cases."""

    tick: int = 0
    cancelled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
