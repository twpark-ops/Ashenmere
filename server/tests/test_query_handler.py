"""Tests for the query handler — dispatching agent queries to data sources."""

from hashlib import sha256
from uuid import uuid4

import pytest
from agentburg_shared.protocol.messages import QueryMessage, QueryType
from sqlalchemy.ext.asyncio import AsyncSession

import agentburg_server.db as _db
from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import (
    MarketOrder,
    OrderSide,
    OrderStatus,
    Property,
    PropertyType,
    Trade,
)
from agentburg_server.models.social import Business, BusinessType, CaseStatus, CaseType, CourtCase
from agentburg_server.services.query_handler import handle_query

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_query(query: QueryType, params: dict | None = None) -> QueryMessage:
    """Build a QueryMessage with a random request_id."""
    return QueryMessage(
        type="query",
        request_id=str(uuid4()),
        query=query,
        params=params or {},
    )


@pytest.fixture(autouse=True)
def _override_db(db_engine):
    """Override the DB session factory for query handler."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    original = _db.get_session_factory

    def _override():
        return factory

    _db.get_session_factory = _override
    yield
    _db.get_session_factory = original


@pytest.fixture
async def agent_a(db_session: AsyncSession) -> Agent:
    """Agent with realistic data for query tests."""
    agent = Agent(
        id=uuid4(),
        name="QueryAlice",
        title="Trader",
        api_token_hash=sha256(b"qa-token").hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=15_000,
        inventory={"wood": 10, "stone": 5},
        location="market_square",
        reputation=600,
        credit_score=700,
        total_earnings=50_000,
        total_losses=5_000,
        total_trades=25,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.fixture
async def agent_b(db_session: AsyncSession) -> Agent:
    """Second agent for target queries."""
    agent = Agent(
        id=uuid4(),
        name="QueryBob",
        title="Builder",
        api_token_hash=sha256(b"qb-token").hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=8_000,
        inventory={},
        location="east_district",
        reputation=450,
        credit_score=500,
        total_trades=10,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


# ---------------------------------------------------------------------------
# MY_BALANCE
# ---------------------------------------------------------------------------


class TestMyBalance:
    async def test_returns_balance(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.MY_BALANCE))
        assert result.data["balance"] == 15_000
        assert result.data["total_earnings"] == 50_000
        assert result.data["total_losses"] == 5_000

    async def test_nonexistent_agent_empty_data(self):
        result = await handle_query(uuid4(), _make_query(QueryType.MY_BALANCE))
        assert result.data == {}


# ---------------------------------------------------------------------------
# MY_INVENTORY
# ---------------------------------------------------------------------------


class TestMyInventory:
    async def test_returns_inventory(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.MY_INVENTORY))
        assert result.data["inventory"] == {"wood": 10, "stone": 5}

    async def test_empty_inventory(self, agent_b: Agent):
        result = await handle_query(agent_b.id, _make_query(QueryType.MY_INVENTORY))
        assert result.data["inventory"] == {}


# ---------------------------------------------------------------------------
# MY_PROPERTIES
# ---------------------------------------------------------------------------


class TestMyProperties:
    async def test_returns_properties(self, agent_a: Agent, db_session: AsyncSession):
        prop = Property(
            id=uuid4(),
            name="Riverside Lot",
            property_type=PropertyType.LAND,
            location="riverside",
            owner_id=agent_a.id,
            market_value=5000,
        )
        db_session.add(prop)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.MY_PROPERTIES))
        assert len(result.data["properties"]) == 1
        assert result.data["properties"][0]["name"] == "Riverside Lot"
        assert result.data["properties"][0]["type"] == "land"

    async def test_no_properties(self, agent_b: Agent):
        result = await handle_query(agent_b.id, _make_query(QueryType.MY_PROPERTIES))
        assert result.data["properties"] == []


# ---------------------------------------------------------------------------
# MARKET_PRICES
# ---------------------------------------------------------------------------


class TestMarketPrices:
    async def test_returns_prices(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.MARKET_PRICES))
        assert "prices" in result.data


# ---------------------------------------------------------------------------
# MARKET_ORDERS
# ---------------------------------------------------------------------------


class TestMarketOrders:
    async def test_returns_open_orders(self, agent_a: Agent, db_session: AsyncSession):
        order = MarketOrder(
            id=uuid4(),
            agent_id=agent_a.id,
            item="wood",
            side=OrderSide.SELL,
            price=120,
            quantity=5,
            filled_quantity=0,
            status=OrderStatus.OPEN,
            tick_created=1,
        )
        db_session.add(order)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.MARKET_ORDERS))
        assert len(result.data["orders"]) >= 1
        found = [o for o in result.data["orders"] if o["item"] == "wood"]
        assert len(found) == 1
        assert found[0]["price"] == 120

    async def test_filter_by_item(self, agent_a: Agent, db_session: AsyncSession):
        for item in ["wood", "stone"]:
            order = MarketOrder(
                id=uuid4(),
                agent_id=agent_a.id,
                item=item,
                side=OrderSide.BUY,
                price=100,
                quantity=1,
                filled_quantity=0,
                status=OrderStatus.OPEN,
                tick_created=1,
            )
            db_session.add(order)
        await db_session.flush()

        result = await handle_query(
            agent_a.id,
            _make_query(QueryType.MARKET_ORDERS, {"item": "stone"}),
        )
        items = [o["item"] for o in result.data["orders"]]
        assert all(i == "stone" for i in items)

    async def test_no_open_orders(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.MARKET_ORDERS))
        assert result.data["orders"] == []


# ---------------------------------------------------------------------------
# AGENT_INFO
# ---------------------------------------------------------------------------


class TestAgentInfo:
    async def test_returns_target_info(self, agent_a: Agent, agent_b: Agent):
        result = await handle_query(
            agent_a.id,
            _make_query(QueryType.AGENT_INFO, {"agent_id": str(agent_b.id)}),
        )
        assert result.data["name"] == "QueryBob"
        assert result.data["reputation"] == 450
        assert result.data["status"] == "active"

    async def test_missing_agent_id_empty_data(self, agent_a: Agent):
        result = await handle_query(
            agent_a.id,
            _make_query(QueryType.AGENT_INFO, {}),
        )
        assert result.data == {}

    async def test_nonexistent_target(self, agent_a: Agent):
        result = await handle_query(
            agent_a.id,
            _make_query(QueryType.AGENT_INFO, {"agent_id": str(uuid4())}),
        )
        assert result.data == {}


# ---------------------------------------------------------------------------
# BANK_RATES
# ---------------------------------------------------------------------------


class TestBankRates:
    async def test_returns_rates(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.BANK_RATES))
        assert result.data["savings_rate"] == 300
        assert result.data["loan_base_rate"] == 1000


# ---------------------------------------------------------------------------
# COURT_CASES
# ---------------------------------------------------------------------------


class TestCourtCases:
    async def test_returns_active_cases(self, agent_a: Agent, agent_b: Agent, db_session: AsyncSession):
        case = CourtCase(
            id=uuid4(),
            case_type=CaseType.FRAUD,
            plaintiff_id=agent_a.id,
            defendant_id=agent_b.id,
            description="Test fraud case for query testing",
            evidence={},
            status=CaseStatus.FILED,
            tick_filed=5,
        )
        db_session.add(case)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.COURT_CASES))
        assert len(result.data["cases"]) == 1
        assert result.data["cases"][0]["type"] == "fraud"

    async def test_no_cases(self, agent_a: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.COURT_CASES))
        assert result.data["cases"] == []


# ---------------------------------------------------------------------------
# BUSINESS_LIST
# ---------------------------------------------------------------------------


class TestBusinessList:
    async def test_returns_active_businesses(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Alice's Bakery",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="main_street",
            capital=3000,
            is_active=True,
            products={"bread": 50},
        )
        db_session.add(biz)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.BUSINESS_LIST))
        assert len(result.data["businesses"]) >= 1
        found = [b for b in result.data["businesses"] if b["name"] == "Alice's Bakery"]
        assert len(found) == 1
        assert found[0]["products"] == {"bread": 50}

    async def test_excludes_inactive(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Closed Shop",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="old_town",
            capital=0,
            is_active=False,
        )
        db_session.add(biz)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.BUSINESS_LIST))
        names = [b["name"] for b in result.data["businesses"]]
        assert "Closed Shop" not in names


# ---------------------------------------------------------------------------
# WORLD_STATUS
# ---------------------------------------------------------------------------


class TestWorldStatus:
    async def test_returns_counts(self, agent_a: Agent, agent_b: Agent):
        result = await handle_query(agent_a.id, _make_query(QueryType.WORLD_STATUS))
        assert result.data["total_agents"] == 2
        assert result.data["active_agents"] == 2
        assert result.data["total_trades"] == 0

    async def test_with_trades(self, agent_a: Agent, agent_b: Agent, db_session: AsyncSession):
        # Create real orders so FK constraints are satisfied
        buy_order = MarketOrder(
            id=uuid4(),
            agent_id=agent_a.id,
            item="wood",
            side=OrderSide.BUY,
            price=100,
            quantity=1,
            filled_quantity=1,
            status=OrderStatus.FILLED,
            tick_created=1,
        )
        sell_order = MarketOrder(
            id=uuid4(),
            agent_id=agent_b.id,
            item="wood",
            side=OrderSide.SELL,
            price=100,
            quantity=1,
            filled_quantity=1,
            status=OrderStatus.FILLED,
            tick_created=1,
        )
        db_session.add_all([buy_order, sell_order])
        await db_session.flush()

        trade = Trade(
            id=uuid4(),
            tick=1,
            item="wood",
            buyer_id=agent_a.id,
            seller_id=agent_b.id,
            price=100,
            quantity=1,
            total=100,
            buy_order_id=buy_order.id,
            sell_order_id=sell_order.id,
        )
        db_session.add(trade)
        await db_session.flush()

        result = await handle_query(agent_a.id, _make_query(QueryType.WORLD_STATUS))
        assert result.data["total_trades"] == 1
