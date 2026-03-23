"""Production system — agents earn income and produce goods each tick.

Each agent receives a base income per tick. Agents at specific locations
produce items matching that location's industry. This prevents economic
collapse by ensuring continuous supply of goods and money.
"""

import logging
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus

logger = logging.getLogger(__name__)

# Base income per macro tick (in coins)
BASE_INCOME = 500

# Location-based production: location → (item, min_qty, max_qty)
PRODUCTION_TABLE: dict[str, list[tuple[str, int, int]]] = {
    "farm": [("wheat", 3, 8), ("meat", 1, 3)],
    "workshop": [("tools", 1, 3), ("iron", 2, 5)],
    "market": [("cloth", 1, 4), ("spices", 0, 2)],
    "dock": [("fish", 3, 7), ("spices", 1, 2)],
    "tavern": [("ale", 2, 6), ("bread", 1, 3)],
    "residential_north": [("wool", 1, 3)],
    "residential_south": [("leather", 1, 3)],
    "bank": [("gold", 0, 1)],
    "courthouse": [],
    "town_center": [("bread", 1, 2)],
}


async def process_production(session: AsyncSession, tick: int) -> int:
    """Give all active agents base income and location-based production.

    Returns the number of agents that received production.
    """
    stmt = select(Agent).where(Agent.status == AgentStatus.ACTIVE)
    result = await session.execute(stmt)
    agents = list(result.scalars().all())

    count = 0
    for agent in agents:
        # Base income
        agent.balance += BASE_INCOME

        # Location-based production
        productions = PRODUCTION_TABLE.get(agent.location, [])
        inventory = dict(agent.inventory or {})
        produced_items = []

        for item, min_qty, max_qty in productions:
            qty = random.randint(min_qty, max_qty)
            if qty > 0:
                inventory[item] = inventory.get(item, 0) + qty
                produced_items.append(f"{qty}x {item}")

        if produced_items:
            agent.inventory = inventory

        count += 1

    if count > 0 and tick % 6 == 0:
        logger.info("Tick %d: %d agents received income (%d coins each)", tick, count, BASE_INCOME)

    return count
