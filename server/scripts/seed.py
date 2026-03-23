"""Seed script — populate the database with initial agents and data.

Usage:
    uv run python server/scripts/seed.py
"""

import asyncio
import logging
import os
import secrets
from hashlib import sha256

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentburg:agentburg@localhost:5432/agentburg",
)

# Diverse agent personalities for emergent drama
AGENTS = [
    {
        "name": "Marco",
        "title": "Merchant Prince",
        "bio": "A shrewd trader who built his fortune from nothing. Trusts numbers, not people.",
        "balance": 50000,
        "location": "market",
    },
    {
        "name": "Yuki",
        "title": "Cautious Banker",
        "bio": "Former accountant who believes in slow, steady wealth. Never takes unnecessary risks.",
        "balance": 30000,
        "location": "bank",
    },
    {
        "name": "Sage",
        "title": "Legal Eagle",
        "bio": "Sharp-tongued lawyer who profits from others' disputes. Knows every loophole.",
        "balance": 25000,
        "location": "courthouse",
    },
    {
        "name": "Rex",
        "title": "Hustler",
        "bio": "Fast-talking dealer who lives on the edge. High risk, high reward. Sometimes crosses the line.",
        "balance": 15000,
        "location": "tavern",
    },
    {
        "name": "Luna",
        "title": "Social Butterfly",
        "bio": "Everyone's friend, nobody's enemy. Trades favors more than goods. Knows all the gossip.",
        "balance": 20000,
        "location": "town_center",
    },
    {
        "name": "Viktor",
        "title": "Industrial Baron",
        "bio": "Believes in production over speculation. Builds things. Employs people. Controls supply.",
        "balance": 40000,
        "location": "workshop",
    },
    {
        "name": "Mei",
        "title": "Farmer",
        "bio": "Simple, honest, hardworking. Grows food, sells at market. Suspicious of city folk.",
        "balance": 12000,
        "location": "farm",
    },
    {
        "name": "Dante",
        "title": "Con Artist",
        "bio": "Charming, persuasive, and completely untrustworthy. Every deal has a hidden angle.",
        "balance": 8000,
        "location": "tavern",
    },
]


async def seed_database() -> None:
    """Seed the database with admin user and diverse agents."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from agentburg_server.models.agent import Agent, AgentTier
    from agentburg_server.models.base import Base
    from agentburg_server.models.user import User

    async with engine.begin() as conn:
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
        logger.info("Created admin user: admin@agentburg.world")

        # 2. Create diverse agents
        from agentburg_server.services.locations import get_location_coords

        tokens = []
        for agent_data in AGENTS:
            raw_token = f"ab_{secrets.token_urlsafe(32)}"
            token_hash = sha256(raw_token.encode()).hexdigest()
            x, y = get_location_coords(agent_data["location"])

            agent = Agent(
                name=agent_data["name"],
                title=agent_data["title"],
                bio=agent_data["bio"],
                owner_id=admin.id,
                api_token_hash=token_hash,
                tier=AgentTier.PLAYER,
                balance=agent_data["balance"],
                location=agent_data["location"],
                pos_x=x,
                pos_y=y,
            )
            session.add(agent)
            tokens.append((agent_data["name"], raw_token))
            logger.info("Created agent: %s (%s) at %s", agent_data["name"], agent_data["title"], agent_data["location"])

        await session.commit()

    await engine.dispose()

    # Print tokens (only shown once)
    logger.info("=" * 60)
    logger.info("AGENT TOKENS (save these — shown only once):")
    for name, token in tokens:
        logger.info("  %s: %s", name, token)
    logger.info("=" * 60)
    logger.info("Seed complete! %d agents created.", len(AGENTS))


if __name__ == "__main__":
    asyncio.run(seed_database())
