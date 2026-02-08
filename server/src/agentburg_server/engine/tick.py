"""World tick engine — the heartbeat of the simulation.

Each tick:
1. Process pending market orders (batch auction)
2. Process pending court cases
3. Process bank interest (every N ticks = 1 sim-day)
4. Broadcast tick updates to all connected agents
5. Log world state snapshot
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.config import settings
from agentburg_server.db import async_session_factory
from agentburg_server.services.market import run_batch_auction
from agentburg_server.services.bank import process_interest
from agentburg_server.services.court import process_pending_cases

logger = logging.getLogger(__name__)


class TickEngine:
    """World simulation tick loop."""

    def __init__(self) -> None:
        self.tick: int = 0
        self.running: bool = False
        self.ticks_per_day: int = settings.ticks_per_day
        self.tick_interval: float = settings.tick_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the tick loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Tick engine started (interval=%.1fs, ticks/day=%d)", self.tick_interval, self.ticks_per_day)

    async def stop(self) -> None:
        """Stop the tick loop gracefully."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Tick engine stopped at tick %d", self.tick)

    async def _run(self) -> None:
        """Main tick loop."""
        while self.running:
            try:
                await self._process_tick()
                self.tick += 1
                await asyncio.sleep(self.tick_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in tick %d", self.tick)
                await asyncio.sleep(self.tick_interval)

    async def _process_tick(self) -> None:
        """Process a single world tick."""
        start = datetime.now(timezone.utc)

        async with async_session_factory() as session:
            # 1. Run batch auction for market orders
            trades = await run_batch_auction(session, self.tick)

            # 2. Process court cases
            verdicts = await process_pending_cases(session, self.tick)

            # 3. Process interest once per sim-day
            interest_processed = 0
            if self.tick > 0 and self.tick % self.ticks_per_day == 0:
                interest_processed = await process_interest(session, self.tick)

            await session.commit()

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.debug(
            "Tick %d processed in %.3fs: %d trades, %d verdicts, %d interest ops",
            self.tick,
            elapsed,
            len(trades),
            len(verdicts),
            interest_processed,
        )

    @property
    def world_time(self) -> datetime:
        """Calculate simulated world time from tick count."""
        # Each tick = 2 seconds real time, ticks_per_day = 720
        # So 1 sim-day = 720 * 2 = 1440 seconds = 24 min real time
        # World starts at midnight
        day = self.tick // self.ticks_per_day
        tick_in_day = self.tick % self.ticks_per_day
        hour = (tick_in_day * 24) // self.ticks_per_day
        minute = ((tick_in_day * 24 * 60) // self.ticks_per_day) % 60
        return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc) + __import__("datetime").timedelta(days=day)


# Singleton
tick_engine = TickEngine()
