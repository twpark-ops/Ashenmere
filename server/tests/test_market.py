"""Tests for the market exchange service — order placement, batch auction, cancellation."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import OrderSide, OrderStatus
from agentburg_server.services.market import (
    cancel_order,
    place_order,
    run_batch_auction,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "MarketAgent",
    balance: int = 10_000,
    inventory: dict | None = None,
) -> Agent:
    """Insert and return a fresh Agent for market tests."""
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
# Order placement
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_place_buy_order(db_session: AsyncSession):
    """Placing a buy order should reserve funds from the agent's balance."""
    agent = await _make_agent(db_session, balance=5_000)

    order = await place_order(
        db_session,
        agent_id=agent.id,
        item="wheat",
        side=OrderSide.BUY,
        price=100,
        quantity=10,
        tick=1,
    )
    await db_session.flush()

    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.OPEN
    assert order.price == 100
    assert order.quantity == 10
    # Balance should be reduced by price * quantity = 1000
    assert agent.balance == 5_000 - (100 * 10)


@pytest.mark.anyio
async def test_place_sell_order(db_session: AsyncSession):
    """Placing a sell order should reserve items from the agent's inventory."""
    agent = await _make_agent(db_session, inventory={"iron": 50})

    order = await place_order(
        db_session,
        agent_id=agent.id,
        item="iron",
        side=OrderSide.SELL,
        price=200,
        quantity=20,
        tick=1,
    )
    await db_session.flush()

    assert order.side == OrderSide.SELL
    assert order.status == OrderStatus.OPEN
    # Inventory should be reduced
    assert agent.inventory["iron"] == 30


@pytest.mark.anyio
async def test_place_order_insufficient_balance(db_session: AsyncSession):
    """A buy order exceeding the agent's balance must raise ValueError."""
    agent = await _make_agent(db_session, balance=100)

    with pytest.raises(ValueError, match="Insufficient balance"):
        await place_order(
            db_session,
            agent_id=agent.id,
            item="gold",
            side=OrderSide.BUY,
            price=50,
            quantity=10,  # total = 500, balance = 100
            tick=1,
        )


@pytest.mark.anyio
async def test_place_order_insufficient_inventory(db_session: AsyncSession):
    """A sell order exceeding inventory must raise ValueError."""
    agent = await _make_agent(db_session, inventory={"wood": 5})

    with pytest.raises(ValueError, match="Insufficient inventory"):
        await place_order(
            db_session,
            agent_id=agent.id,
            item="wood",
            side=OrderSide.SELL,
            price=100,
            quantity=10,
            tick=1,
        )


@pytest.mark.anyio
async def test_place_order_price_limit(db_session: AsyncSession):
    """Price exceeding MAX_ORDER_PRICE (10_000_00 cents) must be rejected."""
    agent = await _make_agent(db_session, balance=999_999_999)

    with pytest.raises(ValueError, match="Price exceeds limit"):
        await place_order(
            db_session,
            agent_id=agent.id,
            item="diamond",
            side=OrderSide.BUY,
            price=10_000_01,  # 1 cent over the limit
            quantity=1,
            tick=1,
        )


@pytest.mark.anyio
async def test_place_order_quantity_limit(db_session: AsyncSession):
    """Quantity exceeding MAX_ORDER_QUANTITY (10_000) must be rejected."""
    agent = await _make_agent(db_session, balance=999_999_999)

    with pytest.raises(ValueError, match="Quantity exceeds limit"):
        await place_order(
            db_session,
            agent_id=agent.id,
            item="sand",
            side=OrderSide.BUY,
            price=1,
            quantity=10_001,
            tick=1,
        )


# ---------------------------------------------------------------------------
# Batch auction matching
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_batch_auction_matching(db_session: AsyncSession):
    """Crossing buy and sell orders should produce a trade at the midpoint price."""
    buyer = await _make_agent(db_session, name="Buyer", balance=50_000)
    seller = await _make_agent(db_session, name="Seller", balance=0, inventory={"wheat": 100})

    # Buyer offers 120 cents, seller asks 80 cents -> midpoint = 100
    await place_order(
        db_session,
        agent_id=buyer.id,
        item="wheat",
        side=OrderSide.BUY,
        price=120,
        quantity=10,
        tick=1,
    )
    await place_order(
        db_session,
        agent_id=seller.id,
        item="wheat",
        side=OrderSide.SELL,
        price=80,
        quantity=10,
        tick=1,
    )
    await db_session.flush()

    trades = await run_batch_auction(db_session, tick=1)
    await db_session.flush()

    assert len(trades) == 1
    trade = trades[0]
    assert trade.item == "wheat"
    assert trade.quantity == 10
    assert trade.price == (120 + 80) // 2  # midpoint = 100
    assert trade.total == 100 * 10  # 1000
    assert trade.buyer_id == buyer.id
    assert trade.seller_id == seller.id

    # Buyer gets refund for price difference: (120 - 100) * 10 = 200
    # Buyer originally had 50_000 - (120 * 10) = 48_800, then +200 refund = 49_000
    assert buyer.balance == 50_000 - (100 * 10)
    # Buyer receives items
    assert buyer.inventory.get("wheat", 0) == 10

    # Seller receives payment: 100 * 10 = 1000
    assert seller.balance == 1000
    assert seller.inventory["wheat"] == 90  # 100 - 10 reserved


@pytest.mark.anyio
async def test_batch_auction_no_match(db_session: AsyncSession):
    """When buy price < sell price, no trade should be created."""
    buyer = await _make_agent(db_session, name="LowBidder", balance=50_000)
    seller = await _make_agent(db_session, name="HighAsker", inventory={"ore": 100})

    await place_order(
        db_session,
        agent_id=buyer.id,
        item="ore",
        side=OrderSide.BUY,
        price=50,
        quantity=10,
        tick=1,
    )
    await place_order(
        db_session,
        agent_id=seller.id,
        item="ore",
        side=OrderSide.SELL,
        price=100,
        quantity=10,
        tick=1,
    )
    await db_session.flush()

    trades = await run_batch_auction(db_session, tick=1)

    assert len(trades) == 0


@pytest.mark.anyio
async def test_batch_auction_self_match(db_session: AsyncSession):
    """An agent's own buy and sell orders must not match each other."""
    agent = await _make_agent(db_session, name="SelfTrader", balance=50_000, inventory={"fish": 100})

    await place_order(
        db_session,
        agent_id=agent.id,
        item="fish",
        side=OrderSide.BUY,
        price=200,
        quantity=5,
        tick=1,
    )
    await place_order(
        db_session,
        agent_id=agent.id,
        item="fish",
        side=OrderSide.SELL,
        price=100,
        quantity=5,
        tick=1,
    )
    await db_session.flush()

    trades = await run_batch_auction(db_session, tick=1)

    assert len(trades) == 0


# ---------------------------------------------------------------------------
# Order cancellation and expiry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_order_refund(db_session: AsyncSession):
    """Cancelling a buy order must refund the reserved balance."""
    agent = await _make_agent(db_session, balance=10_000)
    original_balance = agent.balance

    order = await place_order(
        db_session,
        agent_id=agent.id,
        item="silk",
        side=OrderSide.BUY,
        price=100,
        quantity=5,
        tick=1,
    )
    await db_session.flush()
    assert agent.balance == original_balance - 500  # 100 * 5

    cancelled = await cancel_order(db_session, order_id=order.id, agent_id=agent.id, tick=2)
    await db_session.flush()

    assert cancelled.status == OrderStatus.CANCELLED
    assert agent.balance == original_balance  # fully refunded


@pytest.mark.anyio
async def test_cancel_sell_order_refund(db_session: AsyncSession):
    """Cancelling a sell order must return reserved items to inventory."""
    agent = await _make_agent(db_session, inventory={"cloth": 20})

    order = await place_order(
        db_session,
        agent_id=agent.id,
        item="cloth",
        side=OrderSide.SELL,
        price=50,
        quantity=10,
        tick=1,
    )
    await db_session.flush()
    assert agent.inventory["cloth"] == 10  # 20 - 10 reserved

    cancelled = await cancel_order(db_session, order_id=order.id, agent_id=agent.id, tick=2)
    await db_session.flush()

    assert cancelled.status == OrderStatus.CANCELLED
    assert agent.inventory["cloth"] == 20  # fully restored


@pytest.mark.anyio
async def test_expire_orders_refund(db_session: AsyncSession):
    """Expired buy orders should refund the agent's reserved balance."""
    agent = await _make_agent(db_session, balance=10_000)

    order = await place_order(
        db_session,
        agent_id=agent.id,
        item="spice",
        side=OrderSide.BUY,
        price=200,
        quantity=5,
        tick=1,
        tick_expires=3,  # expires at tick 3
    )
    await db_session.flush()
    assert agent.balance == 10_000 - (200 * 5)  # 9_000

    # Run auction at tick 3 (which triggers _expire_orders internally)
    trades = await run_batch_auction(db_session, tick=3)
    await db_session.flush()

    # Refresh the order to see the status change
    await db_session.refresh(order)
    assert order.status == OrderStatus.EXPIRED
    assert agent.balance == 10_000  # fully refunded
    assert len(trades) == 0


@pytest.mark.anyio
async def test_cancel_other_agents_order_rejected(db_session: AsyncSession):
    """An agent must not be able to cancel another agent's order."""
    agent_a = await _make_agent(db_session, name="AgentA", balance=5_000)
    agent_b = await _make_agent(db_session, name="AgentB", balance=5_000)

    order = await place_order(
        db_session,
        agent_id=agent_a.id,
        item="gem",
        side=OrderSide.BUY,
        price=100,
        quantity=5,
        tick=1,
    )
    await db_session.flush()

    with pytest.raises(ValueError, match="Not your order"):
        await cancel_order(db_session, order_id=order.id, agent_id=agent_b.id, tick=2)
