"""World tick engine — the heartbeat of the simulation.

Each tick:
1. Process pending market orders (batch auction)
2. Process pending court cases
3. Process employment contract payments (per-interval)
4. Process bank interest (every N ticks = 1 sim-day)
5. Broadcast tick updates to all connected agents
6. Log world state snapshot
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import agentburg_server.db as _db
from agentburg_server.config import settings
from agentburg_server.models.agent import Agent
from agentburg_server.models.social import Contract, ContractStatus, ContractType
from agentburg_server.services.bank import process_interest
from agentburg_server.services.court import process_pending_cases
from agentburg_server.services.market import run_batch_auction

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
        start = datetime.now(UTC)

        async with _db.get_session_factory()() as session:
            # 1. Run batch auction for market orders
            trades = await run_batch_auction(session, self.tick)

            # 2. Process court cases
            verdicts = await process_pending_cases(session, self.tick)

            # 3. Process employment contract payments
            payments = await _process_contract_payments(session, self.tick)

            # 4. Process interest once per sim-day
            interest_processed = 0
            if self.tick > 0 and self.tick % self.ticks_per_day == 0:
                interest_processed = await process_interest(session, self.tick)

            await session.commit()

        elapsed = (datetime.now(UTC) - start).total_seconds()

        # Broadcast tick update to connected agents and dashboard viewers
        await self._broadcast_tick_update()
        await self._broadcast_dashboard_update(trades, verdicts, payments, interest_processed)

        if self.tick % 100 == 0 or trades or verdicts:
            logger.info(
                "Tick %d (%.3fs): %d trades, %d verdicts, %d payments, %d interest",
                self.tick, elapsed, len(trades), len(verdicts), payments, interest_processed,
            )

    async def _broadcast_tick_update(self) -> None:
        """Send personalized tick updates to all connected agents.

        Each agent receives their own state (balance, inventory, reputation)
        plus shared market data and any relevant observations.
        """
        from agentburg_server.api.ws import broadcast_to_agent, get_connected_agents
        from agentburg_server.services.market import get_market_prices

        connected = get_connected_agents()
        if not connected:
            return

        async with _db.get_session_factory()() as session:
            # Fetch market data once for all agents
            prices = await get_market_prices(session)
            market_data = {"prices": prices}

            # Send personalized updates to each connected agent
            for agent_id in connected:
                agent = await session.get(Agent, agent_id)
                if agent is None:
                    continue

                update_data = {
                    "type": "tick_update",
                    "tick": self.tick,
                    "world_time": str(self.world_time),
                    "agent": {
                        "agent_id": str(agent.id),
                        "name": agent.name,
                        "balance": agent.balance,
                        "inventory": agent.inventory or {},
                        "reputation": agent.reputation,
                        "credit_score": agent.credit_score,
                        "location": agent.location,
                        "status": agent.status.value,
                    },
                    "market": market_data,
                    "observations": [],
                }
                await broadcast_to_agent(agent_id, update_data)

    async def _broadcast_dashboard_update(
        self,
        trades: list,
        verdicts: list,
        payments: int,
        interest_processed: int,
    ) -> None:
        """Send world summary to dashboard WebSocket viewers."""
        from agentburg_server.api.ws import broadcast_to_dashboard

        data = {
            "type": "tick_update",
            "tick": self.tick,
            "world_time": str(self.world_time),
            "stats": {
                "trades": len(trades),
                "verdicts": len(verdicts),
                "payments": payments,
                "interest_processed": interest_processed,
            },
        }
        await broadcast_to_dashboard(data)

    @property
    def world_time(self) -> datetime:
        """Calculate simulated world time from tick count."""
        day = self.tick // self.ticks_per_day
        tick_in_day = self.tick % self.ticks_per_day
        hour = (tick_in_day * 24) // self.ticks_per_day
        minute = ((tick_in_day * 24 * 60) // self.ticks_per_day) % 60
        return datetime(2026, 1, 1, hour, minute, tzinfo=UTC) + timedelta(days=day)


async def _process_contract_payments(session: AsyncSession, tick: int) -> int:
    """Process salary payments for active employment contracts.

    Checks all ACTIVE EMPLOYMENT contracts. If the tick interval has elapsed
    since last payment (or since contract start), transfers payment_amount
    from employer to employee.
    """
    stmt = select(Contract).where(
        Contract.contract_type == ContractType.EMPLOYMENT,
        Contract.status == ContractStatus.ACTIVE,
        Contract.payment_interval_ticks.is_not(None),
        Contract.payment_amount > 0,
    )
    result = await session.execute(stmt)
    contracts = list(result.scalars().all())

    payments_made = 0
    for contract in contracts:
        interval = contract.payment_interval_ticks
        if interval is None or interval <= 0:
            continue

        # Check if payment is due: elapsed ticks since start is a multiple of interval
        elapsed = tick - contract.tick_start
        if elapsed <= 0 or elapsed % interval != 0:
            continue

        employer = await session.get(Agent, contract.party_a_id)
        employee = await session.get(Agent, contract.party_b_id)
        if employer is None or employee is None:
            continue

        salary = contract.payment_amount

        if employer.balance >= salary:
            employer.balance -= salary
            employee.balance += salary
            payments_made += 1
        else:
            # Employer cannot afford salary — breach of contract
            contract.status = ContractStatus.BREACHED
            employer.reputation = max(0, employer.reputation - 20)
            logger.warning(
                "Contract %s breached: employer %s cannot pay salary %d",
                contract.id, employer.name, salary,
            )

    return payments_made


# Singleton
tick_engine = TickEngine()
