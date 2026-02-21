"""NPC engine — server-side rule-based agents that populate the world.

Each NPC follows a simple deterministic strategy with small random variation.
NPCs generate 0-1 actions per tick processed through the existing action handler.
"""

import abc
import logging
import random
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.config import settings
from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import OrderSide, Trade
from agentburg_server.services.market import place_order

logger = logging.getLogger(__name__)

# NPC naming pools
_MERCHANT_NAMES = ["Trader Hank", "Merchant Mira", "Broker Bill", "Dealer Dana", "Vendor Val"]
_FARMER_NAMES = ["Farmer Gus", "Farmer Faye", "Grower Glen", "Harvester Hazel", "Planter Pete"]
_CONSUMER_NAMES = ["Citizen Carl", "Townie Tess", "Buyer Bob", "Shopper Sam", "Resident Rosa"]

# Strategy constants
MERCHANT_PRICE_SPREAD = 0.10  # 10% spread around average
FARMER_PRODUCE_INTERVAL = 5  # Produce every N ticks
FARMER_SURPLUS_THRESHOLD = 10  # Sell when inventory exceeds this
FARMER_PRODUCE_AMOUNT = 3  # Units produced per cycle
CONSUMER_BUY_INTERVAL = 8  # Buy essentials every N ticks
CONSUMER_DEPOSIT_THRESHOLD = 15000  # Deposit excess above this (in cents)
CONSUMER_DEPOSIT_AMOUNT = 5000  # Amount to deposit each time


class NPCStrategy(abc.ABC):
    """Base class for NPC behavior strategies."""

    strategy_type: str = ""

    @abc.abstractmethod
    async def act(self, session: AsyncSession, agent: Agent, tick: int) -> None:
        """Generate 0-1 actions for this NPC on the given tick."""


class MerchantStrategy(NPCStrategy):
    """Buys below and sells above average market price with small randomness."""

    strategy_type = "merchant"

    async def act(self, session: AsyncSession, agent: Agent, tick: int) -> None:
        prices = await _get_average_prices(session)

        if not prices:
            # No market data yet — place a seed sell order for a common item
            if tick % 10 == 0:
                seed_item = random.choice(["wheat", "wood", "tools"])
                seed_price = random.randint(80, 120)
                inventory = dict(agent.inventory)
                inventory[seed_item] = inventory.get(seed_item, 0) + 2
                agent.inventory = inventory
                try:
                    await place_order(
                        session,
                        agent_id=agent.id,
                        item=seed_item,
                        side=OrderSide.SELL,
                        price=seed_price,
                        quantity=1,
                        tick=tick,
                        tick_expires=tick + 20,
                    )
                except ValueError:
                    pass
            return

        # Pick a random traded item
        item = random.choice(list(prices.keys()))
        avg_price = prices[item]
        if avg_price <= 0:
            return

        # Random jitter +/- 5%
        jitter = random.uniform(-0.05, 0.05)

        # Decide: buy or sell based on balance and inventory
        current_qty = agent.inventory.get(item, 0)

        if current_qty > 0 and random.random() < 0.5:
            # Sell above average
            sell_price = int(avg_price * (1 + MERCHANT_PRICE_SPREAD + jitter))
            sell_price = max(1, sell_price)
            qty = min(current_qty, random.randint(1, 3))
            try:
                await place_order(
                    session,
                    agent_id=agent.id,
                    item=item,
                    side=OrderSide.SELL,
                    price=sell_price,
                    quantity=qty,
                    tick=tick,
                    tick_expires=tick + 20,
                )
            except ValueError as e:
                logger.debug("Merchant NPC %s sell failed: %s", agent.name, e)
        else:
            # Buy below average
            buy_price = int(avg_price * (1 - MERCHANT_PRICE_SPREAD + jitter))
            buy_price = max(1, buy_price)
            qty = random.randint(1, 2)
            total = buy_price * qty
            if agent.balance >= total:
                try:
                    await place_order(
                        session,
                        agent_id=agent.id,
                        item=item,
                        side=OrderSide.BUY,
                        price=buy_price,
                        quantity=qty,
                        tick=tick,
                        tick_expires=tick + 20,
                    )
                except ValueError as e:
                    logger.debug("Merchant NPC %s buy failed: %s", agent.name, e)


class FarmerStrategy(NPCStrategy):
    """Produces goods every N ticks and sells surplus."""

    strategy_type = "farmer"

    def __init__(self) -> None:
        self.product = random.choice(["wheat", "wood"])

    async def act(self, session: AsyncSession, agent: Agent, tick: int) -> None:
        # Produce goods periodically
        if tick % FARMER_PRODUCE_INTERVAL == 0:
            inventory = dict(agent.inventory)
            inventory[self.product] = inventory.get(self.product, 0) + FARMER_PRODUCE_AMOUNT
            agent.inventory = inventory
            logger.debug("Farmer NPC %s produced %d %s", agent.name, FARMER_PRODUCE_AMOUNT, self.product)

        # Sell surplus
        current_qty = agent.inventory.get(self.product, 0)
        if current_qty > FARMER_SURPLUS_THRESHOLD:
            sell_qty = current_qty - FARMER_SURPLUS_THRESHOLD + random.randint(0, 2)
            sell_qty = min(sell_qty, current_qty)
            if sell_qty <= 0:
                return

            prices = await _get_average_prices(session)
            base_price = prices.get(self.product, 100)
            sell_price = max(1, int(base_price * random.uniform(0.9, 1.05)))

            try:
                await place_order(
                    session,
                    agent_id=agent.id,
                    item=self.product,
                    side=OrderSide.SELL,
                    price=sell_price,
                    quantity=sell_qty,
                    tick=tick,
                    tick_expires=tick + 30,
                )
            except ValueError as e:
                logger.debug("Farmer NPC %s sell failed: %s", agent.name, e)


class ConsumerStrategy(NPCStrategy):
    """Periodically buys essentials and deposits excess money."""

    strategy_type = "consumer"

    async def act(self, session: AsyncSession, agent: Agent, tick: int) -> None:
        if tick % CONSUMER_BUY_INTERVAL != 0:
            return

        # Buy essentials (food/tools) if affordable
        prices = await _get_average_prices(session)
        essentials = ["wheat", "tools"]

        for item in essentials:
            current_qty = agent.inventory.get(item, 0)
            if current_qty >= 5:
                continue  # Already stocked up

            base_price = prices.get(item, 100)
            buy_price = max(1, int(base_price * random.uniform(1.0, 1.15)))
            if agent.balance >= buy_price:
                try:
                    await place_order(
                        session,
                        agent_id=agent.id,
                        item=item,
                        side=OrderSide.BUY,
                        price=buy_price,
                        quantity=1,
                        tick=tick,
                        tick_expires=tick + 15,
                    )
                except ValueError as e:
                    logger.debug("Consumer NPC %s buy failed: %s", agent.name, e)
                break  # One purchase per tick


# Strategy registry
STRATEGY_CLASSES: dict[str, type[NPCStrategy]] = {
    "merchant": MerchantStrategy,
    "farmer": FarmerStrategy,
    "consumer": ConsumerStrategy,
}


class NPCEngine:
    """Manages server-side NPC agents and their strategies."""

    def __init__(self) -> None:
        self.npc_ids: list[UUID] = []
        self.strategies: dict[UUID, NPCStrategy] = {}

    async def initialize(self, session: AsyncSession) -> None:
        """Create NPC agents in DB if they don't already exist.

        Called once at server startup. Finds existing NPC_RULE agents
        or creates new ones up to the configured npc_count.
        """
        if settings.npc_count <= 0:
            logger.info("NPC engine disabled (npc_count=0)")
            return

        # Find existing NPC agents
        stmt = select(Agent).where(Agent.tier == AgentTier.NPC_RULE)
        result = await session.execute(stmt)
        existing_npcs = list(result.scalars().all())

        # Map existing NPCs by name for idempotency
        existing_by_name: dict[str, Agent] = {a.name: a for a in existing_npcs}

        # Parse configured NPC types
        npc_types = [t.strip() for t in settings.npc_types.split(",") if t.strip()]
        if not npc_types:
            npc_types = ["merchant", "farmer", "consumer"]

        name_pools = {
            "merchant": list(_MERCHANT_NAMES),
            "farmer": list(_FARMER_NAMES),
            "consumer": list(_CONSUMER_NAMES),
        }

        created = 0
        for i in range(settings.npc_count):
            npc_type = npc_types[i % len(npc_types)]
            pool = name_pools.get(npc_type, _MERCHANT_NAMES)
            name = pool[i % len(pool)] if i < len(pool) * len(npc_types) else f"NPC-{npc_type}-{i}"

            if name in existing_by_name:
                agent = existing_by_name[name]
            else:
                agent = Agent(
                    id=uuid4(),
                    name=name,
                    title=npc_type.capitalize(),
                    bio=f"A server-hosted {npc_type} NPC.",
                    api_token_hash=sha256(f"npc-{name}-{uuid4()}".encode()).hexdigest(),
                    tier=AgentTier.NPC_RULE,
                    status=AgentStatus.ACTIVE,
                    balance=settings.initial_agent_balance,
                    inventory={},
                    location="town_center",
                    reputation=500,
                    credit_score=500,
                )
                session.add(agent)
                created += 1

            self.npc_ids.append(agent.id)

            # Create strategy instance
            strategy_cls = STRATEGY_CLASSES.get(npc_type, MerchantStrategy)
            self.strategies[agent.id] = strategy_cls()

        await session.flush()
        logger.info(
            "NPC engine initialized: %d NPCs (%d new, %d existing)",
            len(self.npc_ids),
            created,
            len(self.npc_ids) - created,
        )

    async def process_npc_actions(self, session: AsyncSession, tick: int) -> int:
        """Run one action cycle for all NPCs. Returns count of NPCs that acted."""
        acted = 0
        for npc_id in self.npc_ids:
            agent = await session.get(Agent, npc_id)
            if agent is None or agent.status != AgentStatus.ACTIVE:
                continue

            strategy = self.strategies.get(npc_id)
            if strategy is None:
                continue

            try:
                await strategy.act(session, agent, tick)
                acted += 1
            except Exception:
                logger.exception("NPC %s (strategy=%s) error on tick %d", agent.name, type(strategy).__name__, tick)

        return acted


async def _get_average_prices(session: AsyncSession) -> dict[str, int]:
    """Get VWAP prices from recent trades (reuses market service logic)."""
    from agentburg_server.services.market import get_market_prices

    return await get_market_prices(session)


# Singleton
npc_engine = NPCEngine()
