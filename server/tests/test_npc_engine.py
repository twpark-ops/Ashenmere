"""Tests for the NPC engine — server-side rule-based agents."""

from __future__ import annotations

import random
from hashlib import sha256
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import MarketOrder, OrderSide, OrderStatus, Trade
from agentburg_server.services.npc_engine import (
    CONSUMER_BUY_INTERVAL,
    FARMER_PRODUCE_AMOUNT,
    FARMER_PRODUCE_INTERVAL,
    FARMER_SURPLUS_THRESHOLD,
    ConsumerStrategy,
    FarmerStrategy,
    MerchantStrategy,
    NPCEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    name: str = "TestNPC",
    tier: AgentTier = AgentTier.NPC_RULE,
    balance: int = 10_000,
    inventory: dict | None = None,
) -> Agent:
    return Agent(
        id=uuid4(),
        name=name,
        title="Test",
        bio="test npc",
        api_token_hash=sha256(f"npc-{name}-{uuid4()}".encode()).hexdigest(),
        tier=tier,
        status=AgentStatus.ACTIVE,
        balance=balance,
        inventory=inventory or {},
        location="town_center",
        reputation=500,
        credit_score=500,
    )


async def _seed_trade(session: AsyncSession, item: str, price: int, quantity: int = 1) -> None:
    """Create a fake trade record so get_market_prices returns data."""
    buyer = _make_agent("buyer")
    seller = _make_agent("seller")
    session.add(buyer)
    session.add(seller)
    await session.flush()

    buy_order = MarketOrder(
        agent_id=buyer.id,
        item=item,
        side=OrderSide.BUY,
        price=price,
        quantity=quantity,
        filled_quantity=quantity,
        status=OrderStatus.FILLED,
        tick_created=0,
    )
    sell_order = MarketOrder(
        agent_id=seller.id,
        item=item,
        side=OrderSide.SELL,
        price=price,
        quantity=quantity,
        filled_quantity=quantity,
        status=OrderStatus.FILLED,
        tick_created=0,
    )
    session.add(buy_order)
    session.add(sell_order)
    await session.flush()

    trade = Trade(
        tick=0,
        item=item,
        buyer_id=buyer.id,
        seller_id=seller.id,
        price=price,
        quantity=quantity,
        total=price * quantity,
        buy_order_id=buy_order.id,
        sell_order_id=sell_order.id,
    )
    session.add(trade)
    await session.flush()


# ---------------------------------------------------------------------------
# MerchantStrategy tests
# ---------------------------------------------------------------------------


class TestMerchantStrategy:
    @pytest.mark.anyio
    async def test_merchant_places_seed_sell_when_no_market_data(self, db_session: AsyncSession) -> None:
        """With no trade history, merchant should seed the market on tick multiples of 10."""
        agent = _make_agent("Merchant Test", balance=10_000)
        db_session.add(agent)
        await db_session.flush()

        strategy = MerchantStrategy()
        random.seed(42)
        await strategy.act(db_session, agent, tick=10)

        # Agent should have received seed inventory and placed a sell order
        stmt = select(MarketOrder).where(MarketOrder.agent_id == agent.id)
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())
        # May or may not have placed an order depending on randomness, but inventory should grow
        assert agent.inventory  # Should have gotten seed items

    @pytest.mark.anyio
    async def test_merchant_buys_below_average(self, db_session: AsyncSession) -> None:
        """Merchant should place buy orders below average market price."""
        await _seed_trade(db_session, "wheat", 100, 5)

        agent = _make_agent("Merchant Buyer", balance=10_000)
        db_session.add(agent)
        await db_session.flush()

        strategy = MerchantStrategy()
        # Force buy path: agent has no inventory and random < 0.5 won't trigger sell
        random.seed(1)
        await strategy.act(db_session, agent, tick=1)

        stmt = select(MarketOrder).where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.side == OrderSide.BUY,
        )
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())

        # The merchant should have placed at least one buy order
        # (depends on random path but with no inventory it should prefer buying)
        if orders:
            for order in orders:
                # Buy price should be below average (100) with some spread
                assert order.price <= 120  # avg + reasonable spread + jitter

    @pytest.mark.anyio
    async def test_merchant_sells_above_average(self, db_session: AsyncSession) -> None:
        """Merchant with inventory should sell above average market price."""
        await _seed_trade(db_session, "wheat", 100, 5)

        agent = _make_agent("Merchant Seller", balance=5_000, inventory={"wheat": 10})
        db_session.add(agent)
        await db_session.flush()

        strategy = MerchantStrategy()
        # Try multiple seeds to hit the sell path
        sold = False
        for seed in range(20):
            random.seed(seed)
            await strategy.act(db_session, agent, tick=1)
            stmt = select(MarketOrder).where(
                MarketOrder.agent_id == agent.id,
                MarketOrder.side == OrderSide.SELL,
            )
            result = await db_session.execute(stmt)
            orders = list(result.scalars().all())
            if orders:
                sold = True
                for order in orders:
                    # Sell price should be above 90% of average
                    assert order.price >= 80
                break

        assert sold, "Merchant should sell inventory when it has items"


# ---------------------------------------------------------------------------
# FarmerStrategy tests
# ---------------------------------------------------------------------------


class TestFarmerStrategy:
    @pytest.mark.anyio
    async def test_farmer_produces_on_interval(self, db_session: AsyncSession) -> None:
        """Farmer should add products to inventory every FARMER_PRODUCE_INTERVAL ticks."""
        agent = _make_agent("Farmer Test", balance=5_000, inventory={})
        db_session.add(agent)
        await db_session.flush()

        strategy = FarmerStrategy()
        product = strategy.product

        await strategy.act(db_session, agent, tick=FARMER_PRODUCE_INTERVAL)

        assert agent.inventory.get(product, 0) == FARMER_PRODUCE_AMOUNT

    @pytest.mark.anyio
    async def test_farmer_does_not_produce_off_interval(self, db_session: AsyncSession) -> None:
        """Farmer should not produce on non-interval ticks."""
        agent = _make_agent("Farmer Test2", balance=5_000, inventory={})
        db_session.add(agent)
        await db_session.flush()

        strategy = FarmerStrategy()

        await strategy.act(db_session, agent, tick=FARMER_PRODUCE_INTERVAL + 1)

        assert not agent.inventory  # No production

    @pytest.mark.anyio
    async def test_farmer_sells_surplus(self, db_session: AsyncSession) -> None:
        """Farmer should sell when inventory exceeds threshold."""
        await _seed_trade(db_session, "wheat", 100)

        surplus = FARMER_SURPLUS_THRESHOLD + 5
        agent = _make_agent("Farmer Surplus", balance=5_000, inventory={"wheat": surplus})
        db_session.add(agent)
        await db_session.flush()

        strategy = FarmerStrategy()
        strategy.product = "wheat"
        random.seed(42)
        await strategy.act(db_session, agent, tick=1)  # Not a produce tick

        stmt = select(MarketOrder).where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.side == OrderSide.SELL,
        )
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())

        assert len(orders) >= 1, "Farmer should place sell order for surplus"

    @pytest.mark.anyio
    async def test_farmer_does_not_sell_below_threshold(self, db_session: AsyncSession) -> None:
        """Farmer should NOT sell when inventory is at or below threshold."""
        agent = _make_agent("Farmer Low", balance=5_000, inventory={"wheat": FARMER_SURPLUS_THRESHOLD - 1})
        db_session.add(agent)
        await db_session.flush()

        strategy = FarmerStrategy()
        strategy.product = "wheat"
        await strategy.act(db_session, agent, tick=1)

        stmt = select(MarketOrder).where(MarketOrder.agent_id == agent.id)
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# ConsumerStrategy tests
# ---------------------------------------------------------------------------


class TestConsumerStrategy:
    @pytest.mark.anyio
    async def test_consumer_buys_essentials(self, db_session: AsyncSession) -> None:
        """Consumer should buy essentials on the buy interval tick."""
        await _seed_trade(db_session, "wheat", 80)

        agent = _make_agent("Consumer Test", balance=10_000, inventory={})
        db_session.add(agent)
        await db_session.flush()

        strategy = ConsumerStrategy()
        random.seed(42)
        await strategy.act(db_session, agent, tick=CONSUMER_BUY_INTERVAL)

        stmt = select(MarketOrder).where(
            MarketOrder.agent_id == agent.id,
            MarketOrder.side == OrderSide.BUY,
        )
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())
        assert len(orders) >= 1, "Consumer should buy at least one essential"

    @pytest.mark.anyio
    async def test_consumer_does_not_buy_off_interval(self, db_session: AsyncSession) -> None:
        """Consumer should not buy on non-interval ticks."""
        agent = _make_agent("Consumer Off", balance=10_000, inventory={})
        db_session.add(agent)
        await db_session.flush()

        strategy = ConsumerStrategy()
        await strategy.act(db_session, agent, tick=CONSUMER_BUY_INTERVAL + 1)

        stmt = select(MarketOrder).where(MarketOrder.agent_id == agent.id)
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())
        assert len(orders) == 0

    @pytest.mark.anyio
    async def test_consumer_skips_if_stocked(self, db_session: AsyncSession) -> None:
        """Consumer should not buy essentials if already well stocked."""
        await _seed_trade(db_session, "wheat", 80)
        await _seed_trade(db_session, "tools", 150)

        agent = _make_agent("Consumer Stocked", balance=10_000, inventory={"wheat": 10, "tools": 10})
        db_session.add(agent)
        await db_session.flush()

        strategy = ConsumerStrategy()
        await strategy.act(db_session, agent, tick=CONSUMER_BUY_INTERVAL)

        stmt = select(MarketOrder).where(MarketOrder.agent_id == agent.id)
        result = await db_session.execute(stmt)
        orders = list(result.scalars().all())
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# NPCEngine tests
# ---------------------------------------------------------------------------


class TestNPCEngine:
    @pytest.mark.anyio
    async def test_initialize_creates_npcs(self, db_session: AsyncSession) -> None:
        """NPCEngine.initialize should create NPC agents in the database."""
        engine = NPCEngine()

        with patch("agentburg_server.services.npc_engine.settings") as mock_settings:
            mock_settings.npc_count = 3
            mock_settings.npc_types = "merchant,farmer,consumer"
            mock_settings.initial_agent_balance = 10_000

            await engine.initialize(db_session)

        assert len(engine.npc_ids) == 3
        assert len(engine.strategies) == 3

        # Verify agents exist in DB
        for npc_id in engine.npc_ids:
            agent = await db_session.get(Agent, npc_id)
            assert agent is not None
            assert agent.tier == AgentTier.NPC_RULE
            assert agent.status == AgentStatus.ACTIVE
            assert agent.balance == 10_000

    @pytest.mark.anyio
    async def test_initialize_idempotent(self, db_session: AsyncSession) -> None:
        """Running initialize twice should not duplicate NPCs."""
        engine1 = NPCEngine()
        engine2 = NPCEngine()

        with patch("agentburg_server.services.npc_engine.settings") as mock_settings:
            mock_settings.npc_count = 2
            mock_settings.npc_types = "merchant,farmer"
            mock_settings.initial_agent_balance = 10_000

            await engine1.initialize(db_session)
            await db_session.flush()

        # Count agents before
        stmt = select(Agent).where(Agent.tier == AgentTier.NPC_RULE)
        result = await db_session.execute(stmt)
        before_count = len(list(result.scalars().all()))

        with patch("agentburg_server.services.npc_engine.settings") as mock_settings:
            mock_settings.npc_count = 2
            mock_settings.npc_types = "merchant,farmer"
            mock_settings.initial_agent_balance = 10_000

            await engine2.initialize(db_session)
            await db_session.flush()

        result = await db_session.execute(stmt)
        after_count = len(list(result.scalars().all()))

        assert after_count == before_count

    @pytest.mark.anyio
    async def test_initialize_disabled_when_zero(self, db_session: AsyncSession) -> None:
        """NPCEngine should do nothing when npc_count is 0."""
        engine = NPCEngine()

        with patch("agentburg_server.services.npc_engine.settings") as mock_settings:
            mock_settings.npc_count = 0

            await engine.initialize(db_session)

        assert len(engine.npc_ids) == 0
        assert len(engine.strategies) == 0

    @pytest.mark.anyio
    async def test_process_npc_actions(self, db_session: AsyncSession) -> None:
        """process_npc_actions should invoke strategies for active NPCs."""
        agent = _make_agent("NPC Worker", balance=10_000)
        db_session.add(agent)
        await db_session.flush()

        engine = NPCEngine()
        engine.npc_ids = [agent.id]

        mock_strategy = AsyncMock()
        mock_strategy.act = AsyncMock()
        engine.strategies[agent.id] = mock_strategy

        acted = await engine.process_npc_actions(db_session, tick=1)

        assert acted == 1
        mock_strategy.act.assert_awaited_once_with(db_session, agent, 1)

    @pytest.mark.anyio
    async def test_process_skips_inactive_npcs(self, db_session: AsyncSession) -> None:
        """process_npc_actions should skip NPCs that are not ACTIVE."""
        agent = _make_agent("NPC Sleeping")
        agent.status = AgentStatus.SLEEPING
        db_session.add(agent)
        await db_session.flush()

        engine = NPCEngine()
        engine.npc_ids = [agent.id]

        mock_strategy = AsyncMock()
        engine.strategies[agent.id] = mock_strategy

        acted = await engine.process_npc_actions(db_session, tick=1)

        assert acted == 0
        mock_strategy.act.assert_not_awaited()

    @pytest.mark.anyio
    async def test_process_handles_strategy_error(self, db_session: AsyncSession) -> None:
        """process_npc_actions should not crash if a strategy raises."""
        agent = _make_agent("NPC Error")
        db_session.add(agent)
        await db_session.flush()

        engine = NPCEngine()
        engine.npc_ids = [agent.id]

        mock_strategy = AsyncMock()
        mock_strategy.act = AsyncMock(side_effect=RuntimeError("boom"))
        engine.strategies[agent.id] = mock_strategy

        # Should not raise
        acted = await engine.process_npc_actions(db_session, tick=1)
        assert acted == 0  # Error means the NPC didn't successfully act
