"""Authentication service — user registration, login, agent token management."""

import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.config import settings
from agentburg_server.models.agent import Agent, AgentTier
from agentburg_server.models.user import User

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against an Argon2id hash."""
    try:
        return _ph.verify(hashed, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: UUID) -> str:
    """Create a JWT access token for a user."""
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def generate_agent_token() -> tuple[str, str]:
    """Generate a raw agent token and its SHA-256 hash.

    Returns:
        (raw_token, token_hash) — raw_token is given to the user, hash is stored in DB.
    """
    raw = f"ab_{secrets.token_urlsafe(32)}"
    hashed = sha256(raw.encode()).hexdigest()
    return raw, hashed


async def register_user(
    session: AsyncSession,
    email: str,
    username: str,
    password: str,
) -> User:
    """Register a new user account."""
    existing = await session.execute(
        select(User).where((User.email == email) | (User.username == username))
    )
    if existing.scalar_one_or_none():
        raise ValueError("Email or username already taken")

    user = User(
        email=email,
        username=username,
        hashed_password=hash_password(password),
    )
    session.add(user)
    await session.flush()
    return user


async def login_user(
    session: AsyncSession,
    email: str,
    password: str,
) -> tuple[User, str]:
    """Authenticate a user and return (user, access_token)."""
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.hashed_password):
        raise ValueError("Invalid email or password")

    if not user.is_active:
        raise ValueError("Account is deactivated")

    token = create_access_token(user.id)
    return user, token


async def create_agent(
    session: AsyncSession,
    owner: User,
    name: str,
    title: str | None = None,
    bio: str | None = None,
) -> tuple[Agent, str]:
    """Create a new agent for a user.

    Returns:
        (agent, raw_token) — raw_token must be given to the user once; it cannot be retrieved later.
    """
    # Check agent limit
    agent_count = len(owner.agents) if owner.agents else 0
    if agent_count >= owner.max_agents:
        raise ValueError(f"Agent limit reached ({owner.max_agents})")

    raw_token, token_hash = generate_agent_token()

    agent = Agent(
        name=name,
        title=title,
        bio=bio,
        owner_id=owner.id,
        api_token_hash=token_hash,
        tier=AgentTier.PLAYER,
        balance=settings.initial_agent_balance,
    )
    session.add(agent)
    await session.flush()
    return agent, raw_token
