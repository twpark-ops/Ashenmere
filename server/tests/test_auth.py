"""Tests for the authentication service — registration, login, agent creation, JWT."""

from __future__ import annotations

from datetime import UTC
from uuid import uuid4

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.config import settings
from agentburg_server.models.agent import AgentTier
from agentburg_server.models.user import User
from agentburg_server.services.auth import (
    create_access_token,
    create_agent,
    generate_agent_token,
    hash_password,
    login_user,
    register_user,
    verify_password,
)

# ---------------------------------------------------------------------------
# Password hashing (synchronous unit tests)
# ---------------------------------------------------------------------------


def test_hash_password_produces_argon2id():
    """hash_password must produce an Argon2id hash recognizable by its prefix."""
    hashed = hash_password("my-secret-pass")
    assert hashed.startswith("$argon2id$")
    assert hashed != "my-secret-pass"


def test_verify_password_correct():
    """verify_password should return True for the correct password."""
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed) is True


def test_verify_password_wrong():
    """verify_password should return False for an incorrect password."""
    hashed = hash_password("right-password")
    assert verify_password("wrong-password", hashed) is False


# ---------------------------------------------------------------------------
# User registration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_user(db_session: AsyncSession):
    """Registering a user should persist the user with an Argon2id-hashed password."""
    user = await register_user(
        db_session,
        email="alice@agentburg.test",
        username="alice",
        password="securepassword",
    )
    await db_session.flush()

    assert user.id is not None
    assert user.email == "alice@agentburg.test"
    assert user.username == "alice"
    assert user.hashed_password.startswith("$argon2id$")
    assert user.is_active is True
    assert user.is_admin is False
    # Password must not be stored in plain text
    assert user.hashed_password != "securepassword"


@pytest.mark.anyio
async def test_duplicate_email(db_session: AsyncSession):
    """Registering with a duplicate email must raise ValueError."""
    await register_user(db_session, email="bob@test.com", username="bob", password="pw1")
    await db_session.flush()

    with pytest.raises(ValueError, match="already taken"):
        await register_user(db_session, email="bob@test.com", username="bob2", password="pw2")


@pytest.mark.anyio
async def test_duplicate_username(db_session: AsyncSession):
    """Registering with a duplicate username must raise ValueError."""
    await register_user(db_session, email="carol@test.com", username="carol", password="pw1")
    await db_session.flush()

    with pytest.raises(ValueError, match="already taken"):
        await register_user(db_session, email="carol2@test.com", username="carol", password="pw2")


# ---------------------------------------------------------------------------
# User login
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_login_user(db_session: AsyncSession):
    """Logging in with correct credentials should return the user and a valid JWT."""
    await register_user(db_session, email="dave@test.com", username="dave", password="davepass")
    await db_session.flush()

    user, token = await login_user(db_session, email="dave@test.com", password="davepass")

    assert user.email == "dave@test.com"
    assert isinstance(token, str)
    assert len(token) > 0

    # Decode the JWT and verify the subject
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(user.id)
    assert "exp" in payload
    assert "iat" in payload


@pytest.mark.anyio
async def test_login_wrong_password(db_session: AsyncSession):
    """Logging in with an incorrect password must raise ValueError."""
    await register_user(db_session, email="eve@test.com", username="eve", password="realpass")
    await db_session.flush()

    with pytest.raises(ValueError, match="Invalid email or password"):
        await login_user(db_session, email="eve@test.com", password="wrongpass")


@pytest.mark.anyio
async def test_login_nonexistent_email(db_session: AsyncSession):
    """Logging in with a non-registered email must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid email or password"):
        await login_user(db_session, email="nobody@test.com", password="anything")


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_agent(db_session: AsyncSession, sample_user: User):
    """Creating an agent should persist it with a hashed token and return the raw token."""
    agent, raw_token = await create_agent(
        db_session,
        owner=sample_user,
        name="TraderBot",
        title="Merchant",
        bio="A trading specialist",
    )
    await db_session.flush()

    assert agent.id is not None
    assert agent.name == "TraderBot"
    assert agent.title == "Merchant"
    assert agent.bio == "A trading specialist"
    assert agent.owner_id == sample_user.id
    assert agent.tier == AgentTier.PLAYER
    assert agent.balance == settings.initial_agent_balance

    # Raw token should start with the "ab_" prefix
    assert raw_token.startswith("ab_")

    # Token hash should be the SHA-256 of the raw token
    from hashlib import sha256

    expected_hash = sha256(raw_token.encode()).hexdigest()
    assert agent.api_token_hash == expected_hash


@pytest.mark.anyio
async def test_create_agent_limit(db_session: AsyncSession, sample_user: User):
    """Creating agents beyond the user's max_agents limit must raise ValueError."""
    # Default max_agents = 3
    for i in range(sample_user.max_agents):
        await create_agent(db_session, owner=sample_user, name=f"Agent-{i}")
        await db_session.flush()
        # Refresh to update the agents relationship
        await db_session.refresh(sample_user, attribute_names=["agents"])

    with pytest.raises(ValueError, match="Agent limit reached"):
        await create_agent(db_session, owner=sample_user, name="OneMoreAgent")


# ---------------------------------------------------------------------------
# JWT access token
# ---------------------------------------------------------------------------


def test_create_access_token():
    """create_access_token should produce a decodable JWT with the correct subject."""
    user_id = uuid4()
    token = create_access_token(user_id)

    assert isinstance(token, str)
    assert len(token) > 0

    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(user_id)
    assert "exp" in payload
    assert "iat" in payload


def test_create_access_token_expiry():
    """The JWT expiry should match the configured jwt_expire_minutes."""
    from datetime import datetime

    user_id = uuid4()
    token = create_access_token(user_id)

    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    iat = datetime.fromtimestamp(payload["iat"], tz=UTC)

    # Token lifetime should be approximately jwt_expire_minutes
    delta_seconds = (exp - iat).total_seconds()
    expected_seconds = settings.jwt_expire_minutes * 60
    # Allow 5 seconds of clock drift
    assert abs(delta_seconds - expected_seconds) < 5


# ---------------------------------------------------------------------------
# Agent token generation
# ---------------------------------------------------------------------------


def test_generate_agent_token_format():
    """generate_agent_token should produce a prefixed token and its SHA-256 hash."""
    raw, hashed = generate_agent_token()

    assert raw.startswith("ab_")
    assert len(raw) > 10  # urlsafe_b64 + prefix

    from hashlib import sha256

    assert hashed == sha256(raw.encode()).hexdigest()


def test_generate_agent_token_uniqueness():
    """Each call to generate_agent_token must produce a unique token."""
    tokens = {generate_agent_token()[0] for _ in range(100)}
    assert len(tokens) == 100
