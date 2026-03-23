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
    "wheat",
    "bread",
    "wood",
    "stone",
    "iron",
    "gold",
    "fish",
    "wool",
    "cloth",
    "tools",
    "leather",
    "meat",
    "ale",
    "medicine",
    "spices",
]



async def seed_database() -> None:
    """Seed the database with initial development data."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Import models after engine is created
    from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
    from agentburg_server.models.base import Base
    from agentburg_server.models.economy import Property, PropertyType
    from agentburg_server.models.user import User

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
