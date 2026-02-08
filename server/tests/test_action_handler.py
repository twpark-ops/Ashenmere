"""Tests for the action handler — dispatching agent actions to services."""

from hashlib import sha256
from uuid import uuid4

import pytest
from agentburg_shared.protocol.messages import ActionMessage, ActionType
from sqlalchemy.ext.asyncio import AsyncSession

import agentburg_server.db as _db
from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import Account, AccountType
from agentburg_server.models.social import Business, BusinessType
from agentburg_server.services.action_handler import handle_action

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(action: ActionType, params: dict | None = None) -> ActionMessage:
    """Build an ActionMessage with a random request_id."""
    return ActionMessage(
        type="action",
        request_id=str(uuid4()),
        action=action,
        params=params or {},
    )


@pytest.fixture(autouse=True)
def _override_db(db_engine):
    """Override the DB session factory for action handler (uses _db module)."""
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
    """Agent with 10,000 cents balance."""
    agent = Agent(
        id=uuid4(),
        name="Alice",
        api_token_hash=sha256(b"alice-token").hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=10_000,
        inventory={"wood": 5},
        location="market_square",
        reputation=500,
        credit_score=600,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.fixture
async def agent_b(db_session: AsyncSession) -> Agent:
    """Second agent for multi-party actions."""
    agent = Agent(
        id=uuid4(),
        name="Bob",
        api_token_hash=sha256(b"bob-token").hexdigest(),
        tier=AgentTier.PLAYER,
        status=AgentStatus.ACTIVE,
        balance=5_000,
        inventory={},
        location="market_square",
        reputation=500,
        credit_score=500,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.fixture
async def checking_account(db_session: AsyncSession, agent_a: Agent) -> Account:
    """Create a checking account for agent_a."""
    acct = Account(
        id=uuid4(),
        agent_id=agent_a.id,
        account_type=AccountType.CHECKING,
        balance=0,
        interest_rate=0,
    )
    db_session.add(acct)
    await db_session.flush()
    return acct


# ---------------------------------------------------------------------------
# Market Actions: BUY / SELL
# ---------------------------------------------------------------------------


class TestMarketActions:
    async def test_buy_success(self, agent_a: Agent):
        msg = _make_msg(ActionType.BUY, {"item": "wood", "price": 100, "quantity": 2})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "order_id" in result.data

    async def test_sell_success(self, agent_a: Agent):
        msg = _make_msg(ActionType.SELL, {"item": "wood", "price": 150, "quantity": 1})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "order_id" in result.data

    async def test_buy_missing_item(self, agent_a: Agent):
        msg = _make_msg(ActionType.BUY, {"price": 100})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing item" in result.message

    async def test_buy_invalid_price(self, agent_a: Agent):
        msg = _make_msg(ActionType.BUY, {"item": "wood", "price": 0})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_sell_no_item(self, agent_a: Agent):
        msg = _make_msg(ActionType.SELL, {"item": "", "price": 100})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False


# ---------------------------------------------------------------------------
# Bank Actions: DEPOSIT / WITHDRAW / BORROW / REPAY
# ---------------------------------------------------------------------------


class TestBankActions:
    async def test_deposit_success(self, agent_a: Agent, checking_account: Account):
        msg = _make_msg(
            ActionType.DEPOSIT,
            {"account_id": str(checking_account.id), "amount": 500},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "new_balance" in result.data

    async def test_deposit_missing_account(self, agent_a: Agent):
        msg = _make_msg(ActionType.DEPOSIT, {"amount": 500})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing account_id" in result.message

    async def test_deposit_invalid_amount(self, agent_a: Agent, checking_account: Account):
        msg = _make_msg(
            ActionType.DEPOSIT,
            {"account_id": str(checking_account.id), "amount": 0},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_withdraw_success(self, agent_a: Agent, checking_account: Account, db_session):
        # First deposit, then withdraw
        checking_account.balance = 1000
        await db_session.flush()
        msg = _make_msg(
            ActionType.WITHDRAW,
            {"account_id": str(checking_account.id), "amount": 500},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True

    async def test_withdraw_missing_account(self, agent_a: Agent):
        msg = _make_msg(ActionType.WITHDRAW, {"amount": 100})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_borrow_success(self, agent_a: Agent):
        msg = _make_msg(ActionType.BORROW, {"amount": 2000})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "loan_account_id" in result.data
        assert "interest_rate" in result.data

    async def test_borrow_invalid_amount(self, agent_a: Agent):
        msg = _make_msg(ActionType.BORROW, {"amount": 0})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Invalid loan amount" in result.message

    async def test_repay_missing_account(self, agent_a: Agent):
        msg = _make_msg(ActionType.REPAY, {"amount": 100})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing account_id" in result.message


# ---------------------------------------------------------------------------
# Legal Actions: SUE
# ---------------------------------------------------------------------------


class TestLegalActions:
    async def test_sue_success(self, agent_a: Agent, agent_b: Agent):
        msg = _make_msg(
            ActionType.SUE,
            {
                "target_id": str(agent_b.id),
                "case_type": "fraud",
                "description": "Sold counterfeit goods",
                "evidence": {"receipt": "fake"},
            },
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "case_id" in result.data

    async def test_sue_missing_target(self, agent_a: Agent):
        msg = _make_msg(ActionType.SUE, {"description": "Something bad"})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing target_id" in result.message

    async def test_sue_missing_description(self, agent_a: Agent, agent_b: Agent):
        msg = _make_msg(ActionType.SUE, {"target_id": str(agent_b.id)})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False


# ---------------------------------------------------------------------------
# Business Actions: START / CLOSE / SET_PRICE / HIRE / FIRE
# ---------------------------------------------------------------------------


class TestBusinessActions:
    async def test_start_business_success(self, agent_a: Agent):
        msg = _make_msg(
            ActionType.START_BUSINESS,
            {"name": "Alice's Shop", "business_type": "shop", "location": "downtown"},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "business_id" in result.data

    async def test_start_business_missing_name(self, agent_a: Agent):
        msg = _make_msg(ActionType.START_BUSINESS, {"business_type": "shop"})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing business name" in result.message

    async def test_close_business_success(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Test Biz",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="downtown",
            capital=3000,
            is_active=True,
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(ActionType.CLOSE_BUSINESS, {"business_id": str(biz.id)})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True

    async def test_close_business_missing_id(self, agent_a: Agent):
        msg = _make_msg(ActionType.CLOSE_BUSINESS, {})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing business_id" in result.message

    async def test_set_price_success(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Price Test Biz",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="market",
            capital=1000,
            is_active=True,
            products={},
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(
            ActionType.SET_PRICE,
            {"business_id": str(biz.id), "item": "bread", "price": 50},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "products" in result.data

    async def test_set_price_missing_fields(self, agent_a: Agent):
        msg = _make_msg(ActionType.SET_PRICE, {"item": "bread"})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing business_id" in result.message

    async def test_hire_success(self, agent_a: Agent, agent_b: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Hire Test Biz",
            business_type=BusinessType.FACTORY,
            owner_id=agent_a.id,
            location="industrial",
            capital=5000,
            is_active=True,
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(
            ActionType.HIRE,
            {
                "employee_id": str(agent_b.id),
                "business_id": str(biz.id),
                "salary": 500,
            },
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "contract_id" in result.data

    async def test_hire_missing_fields(self, agent_a: Agent):
        msg = _make_msg(ActionType.HIRE, {"salary": 100})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_hire_invalid_salary(self, agent_a: Agent, agent_b: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Salary Test Biz",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="downtown",
            capital=1000,
            is_active=True,
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(
            ActionType.HIRE,
            {
                "employee_id": str(agent_b.id),
                "business_id": str(biz.id),
                "salary": 200_000,  # Exceeds MAX_SALARY
            },
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Salary must be" in result.message

    async def test_fire_missing_employee(self, agent_a: Agent):
        msg = _make_msg(ActionType.FIRE, {})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing employee_id" in result.message


# ---------------------------------------------------------------------------
# Trade Offer Actions
# ---------------------------------------------------------------------------


class TestTradeOfferActions:
    async def test_trade_offer_success(self, agent_a: Agent, agent_b: Agent):
        msg = _make_msg(
            ActionType.TRADE_OFFER,
            {
                "target_id": str(agent_b.id),
                "offer_items": {"wood": 2},
                "request_items": {"gold": 1},
            },
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "offer_id" in result.data

    async def test_trade_offer_missing_target(self, agent_a: Agent):
        msg = _make_msg(ActionType.TRADE_OFFER, {"offer_items": {"wood": 1}})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_trade_offer_empty_items(self, agent_a: Agent, agent_b: Agent):
        msg = _make_msg(
            ActionType.TRADE_OFFER,
            {"target_id": str(agent_b.id)},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_accept_offer_missing_id(self, agent_a: Agent):
        msg = _make_msg(ActionType.ACCEPT_OFFER, {})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing offer_id" in result.message

    async def test_reject_offer_missing_id(self, agent_a: Agent):
        msg = _make_msg(ActionType.REJECT_OFFER, {})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing offer_id" in result.message


# ---------------------------------------------------------------------------
# Chat Actions
# ---------------------------------------------------------------------------


class TestChatActions:
    async def test_chat_success(self, agent_a: Agent, agent_b: Agent):
        msg = _make_msg(
            ActionType.CHAT,
            {"target_id": str(agent_b.id), "message": "Hello there!"},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "event_id" in result.data

    async def test_chat_broadcast(self, agent_a: Agent):
        """Chat without target_id is a broadcast."""
        msg = _make_msg(ActionType.CHAT, {"message": "Hello everyone!"})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True

    async def test_chat_empty_message(self, agent_a: Agent):
        msg = _make_msg(ActionType.CHAT, {"message": ""})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Empty chat" in result.message

    async def test_chat_too_long(self, agent_a: Agent):
        msg = _make_msg(ActionType.CHAT, {"message": "x" * 501})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "too long" in result.message


# ---------------------------------------------------------------------------
# Investment Actions
# ---------------------------------------------------------------------------


class TestInvestActions:
    async def test_invest_success(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Invest Target",
            business_type=BusinessType.FACTORY,
            owner_id=agent_a.id,
            location="industrial",
            capital=5000,
            is_active=True,
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(
            ActionType.INVEST,
            {"business_id": str(biz.id), "amount": 1000},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "new_capital" in result.data

    async def test_invest_missing_fields(self, agent_a: Agent):
        msg = _make_msg(ActionType.INVEST, {"amount": 500})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False

    async def test_invest_exceeds_limit(self, agent_a: Agent, db_session: AsyncSession):
        biz = Business(
            id=uuid4(),
            name="Limit Test",
            business_type=BusinessType.SHOP,
            owner_id=agent_a.id,
            location="downtown",
            capital=1000,
            is_active=True,
        )
        db_session.add(biz)
        await db_session.flush()

        msg = _make_msg(
            ActionType.INVEST,
            {"business_id": str(biz.id), "amount": 2_000_000},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "exceeds limit" in result.message


# ---------------------------------------------------------------------------
# Build Actions
# ---------------------------------------------------------------------------


class TestBuildActions:
    async def test_build_success(self, agent_a: Agent):
        msg = _make_msg(
            ActionType.BUILD,
            {"name": "Workshop", "property_type": "building", "location": "east_side"},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert "property_id" in result.data

    async def test_build_missing_name(self, agent_a: Agent):
        msg = _make_msg(ActionType.BUILD, {"property_type": "building"})
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        assert "Missing property name" in result.message


# ---------------------------------------------------------------------------
# IDLE and Edge Cases
# ---------------------------------------------------------------------------


class TestIdleAndEdgeCases:
    async def test_idle_action(self, agent_a: Agent):
        msg = _make_msg(ActionType.IDLE, {})
        result = await handle_action(agent_a.id, msg)
        assert result.success is True
        assert result.action == ActionType.IDLE

    async def test_value_error_is_caught(self, agent_a: Agent):
        """Actions that raise ValueError return failure with the error message."""
        # Try to withdraw more than available from a non-existent account
        msg = _make_msg(
            ActionType.WITHDRAW,
            {"account_id": str(uuid4()), "amount": 100},
        )
        result = await handle_action(agent_a.id, msg)
        assert result.success is False
        # The error comes from the service layer
        assert result.message != ""
