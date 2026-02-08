"""Tests for the social service — trade offers, chat, invest, build."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.social import ContractStatus
from agentburg_server.services.business import start_business
from agentburg_server.services.social import (
    MAX_INVESTMENT_AMOUNT,
    accept_trade_offer,
    build_property,
    create_trade_offer,
    invest_in_business,
    reject_trade_offer,
    send_chat,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "SocialAgent",
    balance: int = 50_000,
    inventory: dict | None = None,
) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=name,
        api_token_hash=sha256(f"token-{name}-{uuid4()}".encode()).hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory=inventory or {},
        location="downtown",
        reputation=500,
        credit_score=700,
    )
    session.add(agent)
    await session.flush()
    return agent


# ---------------------------------------------------------------------------
# create_trade_offer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_trade_offer_basic(db_session: AsyncSession):
    """Creating a trade offer should produce a PROPOSED contract."""
    offerer = await _make_agent(db_session, name="Offerer", inventory={"wheat": 10})
    target = await _make_agent(db_session, name="Target", inventory={"bread": 5})

    contract = await create_trade_offer(
        db_session, offerer.id, target.id,
        offer_items={"wheat": 5},
        request_items={"bread": 2},
        tick=1,
    )
    await db_session.flush()

    assert contract.status == ContractStatus.PROPOSED
    assert contract.party_a_id == offerer.id
    assert contract.party_b_id == target.id
    assert contract.terms["offer"] == {"wheat": 5}
    assert contract.terms["request"] == {"bread": 2}


@pytest.mark.anyio
async def test_create_trade_offer_self(db_session: AsyncSession):
    """Trading with yourself should fail."""
    agent = await _make_agent(db_session, inventory={"wheat": 10})

    with pytest.raises(ValueError, match="Cannot trade with yourself"):
        await create_trade_offer(
            db_session, agent.id, agent.id,
            offer_items={"wheat": 1}, request_items={}, tick=1,
        )


@pytest.mark.anyio
async def test_create_trade_offer_insufficient_inventory(db_session: AsyncSession):
    """Offering more than you have should fail."""
    offerer = await _make_agent(db_session, name="PoorOfferer", inventory={"wheat": 2})
    target = await _make_agent(db_session, name="Target2")

    with pytest.raises(ValueError, match="Insufficient wheat"):
        await create_trade_offer(
            db_session, offerer.id, target.id,
            offer_items={"wheat": 10}, request_items={}, tick=1,
        )


@pytest.mark.anyio
async def test_create_trade_offer_invalid_quantity(db_session: AsyncSession):
    """Non-positive quantities should fail."""
    offerer = await _make_agent(db_session, name="BadQty", inventory={"wheat": 10})
    target = await _make_agent(db_session, name="Target3")

    with pytest.raises(ValueError, match="Invalid quantity"):
        await create_trade_offer(
            db_session, offerer.id, target.id,
            offer_items={"wheat": 0}, request_items={}, tick=1,
        )


# ---------------------------------------------------------------------------
# accept_trade_offer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_accept_trade_offer_basic(db_session: AsyncSession):
    """Accepting a trade should swap items between agents."""
    offerer = await _make_agent(db_session, name="Seller", inventory={"wheat": 10})
    accepter = await _make_agent(db_session, name="Buyer", inventory={"bread": 5})

    contract = await create_trade_offer(
        db_session, offerer.id, accepter.id,
        offer_items={"wheat": 3}, request_items={"bread": 2}, tick=1,
    )
    await db_session.flush()

    accepted = await accept_trade_offer(db_session, accepter.id, contract.id, tick=2)
    await db_session.flush()

    assert accepted.status == ContractStatus.COMPLETED
    assert offerer.inventory.get("wheat", 0) == 7  # 10 - 3
    assert offerer.inventory.get("bread", 0) == 2  # 0 + 2
    assert accepter.inventory.get("wheat", 0) == 3  # 0 + 3
    assert accepter.inventory.get("bread", 0) == 3  # 5 - 2


@pytest.mark.anyio
async def test_accept_trade_offer_not_for_you(db_session: AsyncSession):
    """Accepting an offer meant for someone else should fail."""
    offerer = await _make_agent(db_session, name="A1", inventory={"wheat": 10})
    target = await _make_agent(db_session, name="A2")
    intruder = await _make_agent(db_session, name="A3")

    contract = await create_trade_offer(
        db_session, offerer.id, target.id,
        offer_items={"wheat": 1}, request_items={}, tick=1,
    )
    await db_session.flush()

    with pytest.raises(ValueError, match="not for you"):
        await accept_trade_offer(db_session, intruder.id, contract.id, tick=2)


@pytest.mark.anyio
async def test_accept_trade_offer_insufficient_items(db_session: AsyncSession):
    """Accepting when you don't have enough items should fail."""
    offerer = await _make_agent(db_session, name="Rich", inventory={"wheat": 10})
    accepter = await _make_agent(db_session, name="Poor", inventory={})

    contract = await create_trade_offer(
        db_session, offerer.id, accepter.id,
        offer_items={"wheat": 1}, request_items={"bread": 5}, tick=1,
    )
    await db_session.flush()

    with pytest.raises(ValueError, match="don't have enough"):
        await accept_trade_offer(db_session, accepter.id, contract.id, tick=2)


# ---------------------------------------------------------------------------
# reject_trade_offer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reject_trade_offer_basic(db_session: AsyncSession):
    """Rejecting a trade should terminate the contract."""
    offerer = await _make_agent(db_session, name="Rej1", inventory={"wheat": 10})
    target = await _make_agent(db_session, name="Rej2")

    contract = await create_trade_offer(
        db_session, offerer.id, target.id,
        offer_items={"wheat": 1}, request_items={}, tick=1,
    )
    await db_session.flush()

    rejected = await reject_trade_offer(db_session, target.id, contract.id, tick=2)
    await db_session.flush()

    assert rejected.status == ContractStatus.TERMINATED
    assert rejected.tick_end == 2


@pytest.mark.anyio
async def test_reject_trade_offer_not_for_you(db_session: AsyncSession):
    """Rejecting an offer meant for someone else should fail."""
    offerer = await _make_agent(db_session, name="R1", inventory={"wheat": 10})
    target = await _make_agent(db_session, name="R2")
    intruder = await _make_agent(db_session, name="R3")

    contract = await create_trade_offer(
        db_session, offerer.id, target.id,
        offer_items={"wheat": 1}, request_items={}, tick=1,
    )
    await db_session.flush()

    with pytest.raises(ValueError, match="not for you"):
        await reject_trade_offer(db_session, intruder.id, contract.id, tick=2)


# ---------------------------------------------------------------------------
# send_chat
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_chat_directed(db_session: AsyncSession):
    """Sending a directed chat should create a world event."""
    sender = await _make_agent(db_session, name="ChatSender")
    target = await _make_agent(db_session, name="ChatTarget")

    event = await send_chat(db_session, sender.id, target.id, "Hello!", tick=1)
    await db_session.flush()

    assert event.agent_id == sender.id
    assert event.target_id == target.id
    assert "Hello!" in event.description
    assert event.data["is_public"] is False


@pytest.mark.anyio
async def test_send_chat_public(db_session: AsyncSession):
    """Public chat (no target) should mark is_public."""
    sender = await _make_agent(db_session, name="PublicSender")

    event = await send_chat(db_session, sender.id, None, "Hello world!", tick=1)
    await db_session.flush()

    assert event.target_id is None
    assert event.data["is_public"] is True


@pytest.mark.anyio
async def test_send_chat_truncation(db_session: AsyncSession):
    """Messages longer than 500 chars should be truncated."""
    sender = await _make_agent(db_session, name="LongSender")
    long_msg = "A" * 600

    event = await send_chat(db_session, sender.id, None, long_msg, tick=1)
    await db_session.flush()

    assert len(event.data["message"]) == 500


# ---------------------------------------------------------------------------
# invest_in_business
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_invest_basic(db_session: AsyncSession):
    """Investing should transfer funds from agent to business capital."""
    owner = await _make_agent(db_session, name="BizOwner", balance=50_000)
    investor = await _make_agent(db_session, name="Investor", balance=30_000)
    biz = await start_business(db_session, owner.id, "InvestCo", "shop", "", tick=0)
    await db_session.flush()

    initial_capital = biz.capital
    result = await invest_in_business(db_session, investor.id, biz.id, 10_000, tick=5)
    await db_session.flush()

    assert investor.balance == 20_000
    assert result.capital == initial_capital + 10_000


@pytest.mark.anyio
async def test_invest_insufficient_balance(db_session: AsyncSession):
    """Investing more than your balance should fail."""
    owner = await _make_agent(db_session, name="RichOwner", balance=50_000)
    investor = await _make_agent(db_session, name="BrokeInvestor", balance=100)
    biz = await start_business(db_session, owner.id, "BigCo", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="Insufficient balance"):
        await invest_in_business(db_session, investor.id, biz.id, 10_000, tick=5)


@pytest.mark.anyio
async def test_invest_exceeds_limit(db_session: AsyncSession):
    """Investment exceeding MAX_INVESTMENT_AMOUNT should fail."""
    owner = await _make_agent(db_session, name="MaxOwner", balance=50_000)
    investor = await _make_agent(db_session, name="BigInvestor", balance=2_000_000)
    biz = await start_business(db_session, owner.id, "MaxCo", "shop", "", tick=0)
    await db_session.flush()

    with pytest.raises(ValueError, match="exceeds limit"):
        await invest_in_business(db_session, investor.id, biz.id, MAX_INVESTMENT_AMOUNT + 1, tick=5)


@pytest.mark.anyio
async def test_invest_closed_business(db_session: AsyncSession):
    """Investing in a closed business should fail."""
    owner = await _make_agent(db_session, name="ClosedOwner", balance=50_000)
    investor = await _make_agent(db_session, name="LateInvestor", balance=30_000)
    biz = await start_business(db_session, owner.id, "ClosedCo", "shop", "", tick=0)
    await db_session.flush()
    from agentburg_server.services.business import close_business as close_biz
    await close_biz(db_session, owner.id, biz.id, tick=5)
    await db_session.flush()

    with pytest.raises(ValueError, match="closed"):
        await invest_in_business(db_session, investor.id, biz.id, 1_000, tick=10)


# ---------------------------------------------------------------------------
# build_property
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_property_basic(db_session: AsyncSession):
    """Building a property should deduct cost and create the property."""
    agent = await _make_agent(db_session, balance=50_000)
    prop = await build_property(db_session, agent.id, "MyHouse", "house", "uptown", tick=0)
    await db_session.flush()

    assert prop.name == "MyHouse"
    assert prop.owner_id == agent.id
    assert agent.balance < 50_000


@pytest.mark.anyio
async def test_build_property_insufficient_funds(db_session: AsyncSession):
    """Building without enough funds should fail."""
    agent = await _make_agent(db_session, balance=100)
    with pytest.raises(ValueError, match="Need .* cents"):
        await build_property(db_session, agent.id, "TooPoor", "factory", "", tick=0)


@pytest.mark.anyio
async def test_build_property_invalid_type(db_session: AsyncSession):
    """Building with an invalid property type should fail."""
    agent = await _make_agent(db_session, balance=50_000)
    with pytest.raises(ValueError, match="Invalid property type"):
        await build_property(db_session, agent.id, "BadProp", "spaceship", "", tick=0)
