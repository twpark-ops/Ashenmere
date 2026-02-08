"""Seed script — populate the database with initial data for development.

Usage:
    python -m scripts.seed
    # or
    uv run python server/scripts/seed.py
"""

import asyncio
import logging
import secrets
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql+asyncpg://agentburg:agentburg@localhost:5432/agentburg"

# Initial market items
ITEMS = [
    "wheat", "bread", "wood", "stone", "iron",
    "gold", "fish", "wool", "cloth", "tools",
    "leather", "meat", "ale", "medicine", "spices",
]

# NPC agent configs (Tier 3 rule-based)
NPC_AGENTS = [
    {"name": "Baker Bob", "title": "Baker", "bio": "Buys wheat, sells bread."},
    {"name": "Farmer Alice", "title": "Farmer", "bio": "Grows wheat and raises livestock."},
    {"name": "Miner Tom", "title": "Miner", "bio": "Extracts iron, stone, and gold from the hills."},
    {"name": "Fisher Jin", "title": "Fisher", "bio": "Catches fish at the harbor."},
    {"name": "Smith Hanna", "title": "Blacksmith", "bio": "Forges tools from iron and wood."},
    {"name": "Merchant Leo", "title": "Merchant", "bio": "Buys low, sells high. Always."},
    {"name": "Weaver Rosa", "title": "Weaver", "bio": "Turns wool into fine cloth."},
    {"name": "Healer Doc", "title": "Healer", "bio": "Brews medicine from herbs and spices."},
    {"name": "Brewer Kurt", "title": "Brewer", "bio": "Turns wheat into the finest ale."},
    {"name": "Butcher Pete", "title": "Butcher", "bio": "Processes meat and leather."},
]


async def seed_database() -> None:
    """Seed the database with initial development data."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Import models after engine is created
    from agentburg_server.models.base import Base
    from agentburg_server.models.user import User
    from agentburg_server.models.agent import Agent, AgentTier, AgentStatus
    from agentburg_server.models.economy import Property, PropertyType

    async with engine.begin() as conn:
        # Create all tables (idempotent if they already exist)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # 1. Create admin user
        from argon2 import PasswordHasher

        ph = PasswordHasher()
        admin = User(
            email="admin@agentburg.world",
            username="admin",
            hashed_password=ph.hash("admin123"),
            is_admin=True,
            max_agents=100,
        )
        session.add(admin)
        await session.flush()
        logger.info("Created admin user: admin@agentburg.world / admin123")

        # 2. Create NPC agents
        for npc in NPC_AGENTS:
            raw_token = f"ab_{secrets.token_urlsafe(32)}"
            token_hash = sha256(raw_token.encode()).hexdigest()

            agent = Agent(
                name=npc["name"],
                title=npc["title"],
                bio=npc["bio"],
                owner_id=None,  # NPC — no owner
                api_token_hash=token_hash,
                tier=AgentTier.NPC_RULE,
                status=AgentStatus.ACTIVE,
                balance=50000,  # NPCs start with more money
                inventory={
                    "wheat": 20, "bread": 10, "wood": 15, "stone": 10,
                    "iron": 5, "fish": 10, "tools": 3,
                },
            )
            session.add(agent)
            logger.info("Created NPC: %s (%s)", npc["name"], npc["title"])

        # 3. Create initial properties
        locations = ["town_center", "harbor", "market_square", "hillside", "farmland"]
        properties_data = [
            ("Town Hall", PropertyType.BUILDING, "town_center", 100000),
            ("Harbor Warehouse", PropertyType.BUILDING, "harbor", 50000),
            ("Market Stall #1", PropertyType.SHOP, "market_square", 15000),
            ("Market Stall #2", PropertyType.SHOP, "market_square", 15000),
            ("Market Stall #3", PropertyType.SHOP, "market_square", 15000),
            ("Vacant Land A", PropertyType.LAND, "hillside", 5000),
            ("Vacant Land B", PropertyType.LAND, "farmland", 4000),
            ("Vacant Land C", PropertyType.LAND, "farmland", 4000),
            ("Old House", PropertyType.HOUSE, "town_center", 8000),
            ("Cottage", PropertyType.HOUSE, "hillside", 6000),
        ]
        for name, ptype, loc, value in properties_data:
            prop = Property(
                name=name,
                property_type=ptype,
                location=loc,
                owner_id=None,  # Available for purchase
                market_value=value,
                is_for_sale=True,
                asking_price=value,
            )
            session.add(prop)

        logger.info("Created %d properties", len(properties_data))

        await session.commit()

    await engine.dispose()
    logger.info("Seed complete!")


if __name__ == "__main__":
    asyncio.run(seed_database())
