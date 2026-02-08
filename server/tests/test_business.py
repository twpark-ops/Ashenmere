"""Tests for the business service — start, close, hire, fire, set prices."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.social import BusinessType, ContractStatus
from agentburg_server.services.business import (
    MAX_BUSINESSES_PER_AGENT,
    MAX_SALARY,
    close_business,
    fire_agent,
    hire_agent,
    set_price,
    start_business,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "BizAgent",
    balance: int = 50_000,
) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=name,
        api_token_hash=sha256(f"token-{name}-{uuid4()}".encode()).hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory={},
        location="downtown",
        reputation=500,
        credit_score=700,
    )
    session.add(agent)
    await session.flush()
    return agent


# ---------------------------------------------------------------------------
# start_business
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_business_basic(db_session: AsyncSession):
    """Starting a business should deduct cost and return a Business."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "TestShop", "shop", "downtown", tick=0)
    await db_session.flush()

    assert biz.name == "TestShop"
    assert biz.business_type == BusinessType.SHOP
    assert biz.owner_id == agent.id
    assert biz.is_active is True
    assert agent.balance < 50_000  # startup cost deducted


@pytest.mark.anyio
async def test_start_business_insufficient_funds(db_session: AsyncSession):
    """Starting a business without enough funds should fail."""
    agent = await _make_agent(db_session, balance=100)
    with pytest.raises(ValueError, match="Need .* cents"):
        await start_business(db_session, agent.id, "TooExpensive", "factory", "", tick=0)


@pytest.mark.anyio
async def test_start_business_invalid_type(db_session: AsyncSession):
    """Starting a business with an invalid type should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    with pytest.raises(ValueError, match="Invalid business type"):
        await start_business(db_session, agent.id, "BadType", "nonexistent", "", tick=0)


@pytest.mark.anyio
async def test_start_business_empty_name(db_session: AsyncSession):
    """Starting a business with empty name should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    with pytest.raises(ValueError, match="Business name must be"):
        await start_business(db_session, agent.id, "", "shop", "", tick=0)


@pytest.mark.anyio
async def test_start_business_name_too_long(db_session: AsyncSession):
    """Starting a business with a name > 100 chars should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    with pytest.raises(ValueError, match="Business name must be"):
        await start_business(db_session, agent.id, "X" * 101, "shop", "", tick=0)


@pytest.mark.anyio
async def test_start_business_max_limit(db_session: AsyncSession):
    """Exceeding MAX_BUSINESSES_PER_AGENT should fail."""
    agent = await _make_agent(db_session, balance=500_000)
    for i in range(MAX_BUSINESSES_PER_AGENT):
        await start_business(db_session, agent.id, f"Biz{i}", "service", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="Cannot own more than"):
        await start_business(db_session, agent.id, "OneMore", "service", "", tick=0)


# ---------------------------------------------------------------------------
# close_business
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_business_basic(db_session: AsyncSession):
    """Closing a business should return 50% capital and deactivate."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "ToClose", "shop", "", tick=0)
    await db_session.flush()

    balance_after_start = agent.balance
    capital = biz.capital

    closed = await close_business(db_session, agent.id, biz.id, tick=10)
    await db_session.flush()

    assert closed.is_active is False
    assert closed.capital == 0
    assert agent.balance == balance_after_start + capital // 2


@pytest.mark.anyio
async def test_close_business_not_owner(db_session: AsyncSession):
    """Closing someone else's business should fail."""
    owner = await _make_agent(db_session, name="Owner", balance=50_000)
    other = await _make_agent(db_session, name="Other", balance=50_000)
    biz = await start_business(db_session, owner.id, "NotYours", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="don't own"):
        await close_business(db_session, other.id, biz.id, tick=10)


@pytest.mark.anyio
async def test_close_business_already_closed(db_session: AsyncSession):
    """Closing an already closed business should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "CloseTwice", "shop", "", tick=0)
    await db_session.flush()
    await close_business(db_session, agent.id, biz.id, tick=10)
    await db_session.flush()

    with pytest.raises(ValueError, match="already closed"):
        await close_business(db_session, agent.id, biz.id, tick=20)


# ---------------------------------------------------------------------------
# set_price
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_price_basic(db_session: AsyncSession):
    """Setting a price should update the products catalog."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "PriceShop", "shop", "", tick=0)
    await db_session.flush()

    updated = await set_price(db_session, agent.id, biz.id, "bread", 100)
    assert updated.products["bread"] == 100


@pytest.mark.anyio
async def test_set_price_remove_item(db_session: AsyncSession):
    """Setting price to 0 should remove item from catalog."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "RemShop", "shop", "", tick=0)
    await db_session.flush()

    await set_price(db_session, agent.id, biz.id, "bread", 100)
    await set_price(db_session, agent.id, biz.id, "bread", 0)
    assert "bread" not in biz.products


@pytest.mark.anyio
async def test_set_price_negative(db_session: AsyncSession):
    """Setting a negative price should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "NegShop", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="negative"):
        await set_price(db_session, agent.id, biz.id, "bread", -10)


@pytest.mark.anyio
async def test_set_price_not_owner(db_session: AsyncSession):
    """Setting price on someone else's business should fail."""
    owner = await _make_agent(db_session, name="POwner", balance=50_000)
    other = await _make_agent(db_session, name="POther", balance=50_000)
    biz = await start_business(db_session, owner.id, "OwnedShop", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="don't own"):
        await set_price(db_session, other.id, biz.id, "bread", 100)


# ---------------------------------------------------------------------------
# hire_agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hire_agent_basic(db_session: AsyncSession):
    """Hiring an agent should create an employment contract."""
    employer = await _make_agent(db_session, name="Boss", balance=50_000)
    employee = await _make_agent(db_session, name="Worker", balance=1_000)
    biz = await start_business(db_session, employer.id, "HireCo", "shop", "", tick=0)
    await db_session.flush()

    contract = await hire_agent(db_session, employer.id, employee.id, biz.id, 5_000, tick=10)
    await db_session.flush()

    assert contract.party_a_id == employer.id
    assert contract.party_b_id == employee.id
    assert contract.payment_amount == 5_000
    assert contract.status == ContractStatus.ACTIVE
    assert biz.employees == 1


@pytest.mark.anyio
async def test_hire_self(db_session: AsyncSession):
    """Hiring yourself should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    biz = await start_business(db_session, agent.id, "SelfHire", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="Cannot hire yourself"):
        await hire_agent(db_session, agent.id, agent.id, biz.id, 1_000, tick=10)


@pytest.mark.anyio
async def test_hire_salary_too_high(db_session: AsyncSession):
    """Salary above MAX_SALARY should fail."""
    employer = await _make_agent(db_session, name="HighPay", balance=50_000)
    employee = await _make_agent(db_session, name="Lucky", balance=1_000)
    biz = await start_business(db_session, employer.id, "HighPayCo", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="Salary must be"):
        await hire_agent(db_session, employer.id, employee.id, biz.id, MAX_SALARY + 1, tick=10)


@pytest.mark.anyio
async def test_hire_already_employed(db_session: AsyncSession):
    """Hiring the same agent twice should fail."""
    employer = await _make_agent(db_session, name="DupBoss", balance=50_000)
    employee = await _make_agent(db_session, name="DupWorker", balance=1_000)
    biz = await start_business(db_session, employer.id, "DupCo", "shop", "", tick=0)
    await db_session.flush()

    await hire_agent(db_session, employer.id, employee.id, biz.id, 1_000, tick=10)
    await db_session.flush()

    with pytest.raises(ValueError, match="already employed"):
        await hire_agent(db_session, employer.id, employee.id, biz.id, 2_000, tick=20)


# ---------------------------------------------------------------------------
# fire_agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fire_agent_basic(db_session: AsyncSession):
    """Firing should terminate the contract and decrement employee count."""
    employer = await _make_agent(db_session, name="FireBoss", balance=50_000)
    employee = await _make_agent(db_session, name="Fired", balance=1_000)
    biz = await start_business(db_session, employer.id, "FireCo", "shop", "", tick=0)
    await db_session.flush()

    await hire_agent(db_session, employer.id, employee.id, biz.id, 1_000, tick=10)
    await db_session.flush()
    assert biz.employees == 1

    contract = await fire_agent(db_session, employer.id, employee.id, tick=20)
    await db_session.flush()

    assert contract.status == ContractStatus.TERMINATED
    assert contract.tick_end == 20
    assert biz.employees == 0


@pytest.mark.anyio
async def test_fire_no_contract(db_session: AsyncSession):
    """Firing without an active contract should fail."""
    employer = await _make_agent(db_session, name="NoBoss", balance=50_000)
    employee = await _make_agent(db_session, name="NotHired", balance=1_000)

    with pytest.raises(ValueError, match="No active employment contract"):
        await fire_agent(db_session, employer.id, employee.id, tick=10)
