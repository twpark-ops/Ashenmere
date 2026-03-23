"""Shared test fixtures for Ashenmere server tests.

Provides an async SQLite in-memory database, session management,
FastAPI TestClient with DI overrides, and reusable sample entities.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from hashlib import sha256
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.base import Base
from agentburg_server.models.user import User


# Force asyncio backend only — SQLAlchemy async does not support trio
@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# SQLite type compatibility for PostgreSQL-specific column types
# ---------------------------------------------------------------------------


@compiles(PGUUID, "sqlite")
def _compile_pguuid_sqlite(type_: PGUUID, compiler, **kw):  # noqa: ARG001
    """Render PostgreSQL UUID as CHAR(36) on SQLite."""
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler, **kw):  # noqa: ARG001
    """Render PostgreSQL JSONB as JSON on SQLite."""
    return "JSON"


# ---------------------------------------------------------------------------
# Database engine and session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def db_engine():
    """Create an async SQLite in-memory engine with all tables.

    Uses StaticPool so the same in-memory database is shared across
    the entire connection lifecycle of a single test.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL mode and foreign keys on every raw connection
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession]:
    """Yield an async session bound to the test engine.

    Rolls back all changes after each test for full isolation.
    """
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# FastAPI test client with dependency override
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def test_client(db_engine, db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Provide an async HTTPX client wired to the FastAPI app.

    Overrides both the ``get_session`` DI dependency and the
    ``get_session_factory`` used by WebSocket handlers so all
    database access goes through the test SQLite engine.
    """
    import agentburg_server.db as db_module
    from agentburg_server.db import get_session
    from agentburg_server.main import app

    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session

    # Override session factory for WebSocket handlers (which use get_session_factory())
    test_session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    original_get_session_factory = db_module.get_session_factory

    def _override_get_session_factory():
        return test_session_factory

    db_module.get_session_factory = _override_get_session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    db_module.get_session_factory = original_get_session_factory


# ---------------------------------------------------------------------------
# Sample entity fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def sample_agent(db_session: AsyncSession) -> Agent:
    """Create and persist a realistic test Agent."""
    agent = Agent(
        id=uuid4(),
        name="TestAgent",
        title="Merchant",
        bio="A diligent test agent for unit testing.",
        api_token_hash=sha256(b"test-token-secret").hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=10_000,
        inventory={},
        location="downtown",
        reputation=500,
        credit_score=700,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.fixture(scope="function")
async def sample_user(db_session: AsyncSession) -> User:
    """Create and persist a test User with an Argon2id-hashed password.

    Plain-text password for test assertions: ``testpassword123``
    """
    from argon2 import PasswordHasher

    ph = PasswordHasher()
    user = User(
        id=uuid4(),
        email="testuser@agentburg.test",
        username="testuser",
        hashed_password=ph.hash("testpassword123"),
    )
    db_session.add(user)
    await db_session.flush()
    return user
