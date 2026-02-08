"""Built-in plugin: track economy statistics per tick."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.plugins.base import Plugin, PluginMetadata

logger = logging.getLogger(__name__)


@dataclass
class TickStats:
    """Statistics for a single tick."""

    trades: int = 0
    verdicts: int = 0
    payments: int = 0
    interest: int = 0
    trade_volume: int = 0  # total value of trades
    guilty_verdicts: int = 0
    fines_collected: int = 0


class EconomyStatsPlugin(Plugin):
    """Track economy statistics across ticks.

    Provides a rolling window of per-tick stats that can be queried
    by the dashboard or other systems.
    """

    metadata = PluginMetadata(
        name="economy_stats",
        version="1.0.0",
        description="Track economy statistics per tick",
        author="AgentBurg",
        priority=50,  # Run early to capture data
    )

    def __init__(self, *, window_size: int = 1000) -> None:
        self._window_size = window_size
        self._stats: dict[int, TickStats] = {}
        self._total_trades = 0
        self._total_volume = 0
        self._total_verdicts = 0
        self._total_fines = 0

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
        """Record per-tick economy statistics."""
        stats = self._stats.get(tick, TickStats())
        stats.trades = trades
        stats.verdicts = verdicts
        stats.payments = payments
        stats.interest = interest
        self._stats[tick] = stats
        self._total_trades += trades
        self._total_verdicts += verdicts

        # Prune old ticks beyond window
        if len(self._stats) > self._window_size:
            oldest = min(self._stats.keys())
            del self._stats[oldest]

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
        """Accumulate trade volume."""
        volume = price * quantity
        self._total_volume += volume

        stats = self._stats.get(tick, TickStats())
        stats.trade_volume += volume
        self._stats[tick] = stats

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
        """Track verdict outcomes and fines."""
        stats = self._stats.get(tick, TickStats())
        if guilty:
            stats.guilty_verdicts += 1
            stats.fines_collected += fine
            self._total_fines += fine
        self._stats[tick] = stats

    # -- Query interface (not a hook, called directly) -----------------------

    def get_tick_stats(self, tick: int) -> TickStats | None:
        """Get stats for a specific tick."""
        return self._stats.get(tick)

    @property
    def summary(self) -> dict[str, int]:
        """Get cumulative economy summary."""
        return {
            "total_trades": self._total_trades,
            "total_volume": self._total_volume,
            "total_verdicts": self._total_verdicts,
            "total_fines": self._total_fines,
            "ticks_tracked": len(self._stats),
        }

    def recent_stats(self, n: int = 10) -> list[tuple[int, TickStats]]:
        """Get the most recent N tick stats."""
        sorted_ticks = sorted(self._stats.keys(), reverse=True)[:n]
        return [(t, self._stats[t]) for t in sorted_ticks]
