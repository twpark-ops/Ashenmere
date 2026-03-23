"""Production seed — creates admin user only. No test agents.

Usage:
    uv run -- python -c "import sys; sys.path.insert(0,'server/src'); exec(open('server/scripts/seed_production.py').read())"
"""

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentburg:agentburg@localhost:5432/agentburg",
)


async def seed_production() -> None:
    """Create admin user for production. Agents are created by users via API."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from agentburg_server.models.base import Base
    from agentburg_server.models.user import User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        from argon2 import PasswordHasher

        admin_password = os.getenv("ADMIN_PASSWORD", "change-me-in-production")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@ashenmere.world")

        ph = PasswordHasher()
        admin = User(
            email=admin_email,
            username="admin",
            hashed_password=ph.hash(admin_password),
            is_admin=True,
            max_agents=1000,
        )
        session.add(admin)
        await session.commit()
        logger.info("Production seed complete. Admin: %s", admin_email)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed_production())
