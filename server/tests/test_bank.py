"""Tests for the banking service — deposits, withdrawals, loans, interest processing."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import Account, AccountType
from agentburg_server.services.bank import (
    deposit,
    open_account,
    process_interest,
    repay_loan,
    request_loan,
    withdraw,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "BankAgent",
    balance: int = 10_000,
    credit_score: int = 700,
) -> Agent:
    """Insert and return a fresh Agent for banking tests."""
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
        credit_score=credit_score,
    )
    session.add(agent)
    await session.flush()
    return agent


async def _open_checking(session: AsyncSession, agent: Agent, initial: int = 0) -> Account:
    """Open a checking account for an agent with an optional initial deposit."""
    account = await open_account(session, agent.id, AccountType.CHECKING, initial)
    await session.flush()
    return account


async def _open_savings(session: AsyncSession, agent: Agent, initial: int = 0) -> Account:
    """Open a savings account for an agent."""
    account = await open_account(session, agent.id, AccountType.SAVINGS, initial)
    await session.flush()
    return account


# ---------------------------------------------------------------------------
# Deposit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deposit(db_session: AsyncSession):
    """Depositing funds should move money from agent wallet to account."""
    agent = await _make_agent(db_session, balance=5_000)
    account = await _open_checking(db_session, agent)

    result = await deposit(db_session, agent.id, account.id, amount=2_000, tick=1)
    await db_session.flush()

    assert result.balance == 2_000
    assert agent.balance == 3_000  # 5_000 - 2_000


@pytest.mark.anyio
async def test_insufficient_deposit(db_session: AsyncSession):
    """Depositing more than the agent's wallet balance must raise ValueError."""
    agent = await _make_agent(db_session, balance=500)
    account = await _open_checking(db_session, agent)

    with pytest.raises(ValueError, match="Insufficient balance"):
        await deposit(db_session, agent.id, account.id, amount=1_000, tick=1)


@pytest.mark.anyio
async def test_deposit_zero_amount(db_session: AsyncSession):
    """Depositing zero or negative amount must raise ValueError."""
    agent = await _make_agent(db_session, balance=5_000)
    account = await _open_checking(db_session, agent)

    with pytest.raises(ValueError, match="must be positive"):
        await deposit(db_session, agent.id, account.id, amount=0, tick=1)

    with pytest.raises(ValueError, match="must be positive"):
        await deposit(db_session, agent.id, account.id, amount=-100, tick=1)


# ---------------------------------------------------------------------------
# Withdrawal tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_withdraw(db_session: AsyncSession):
    """Withdrawing funds should move money from account to agent wallet."""
    agent = await _make_agent(db_session, balance=5_000)
    account = await _open_checking(db_session, agent, initial=3_000)
    # After opening with 3_000 deposit: agent.balance=2_000, account.balance=3_000

    result = await withdraw(db_session, agent.id, account.id, amount=1_000, tick=1)
    await db_session.flush()

    assert result.balance == 2_000  # 3_000 - 1_000
    assert agent.balance == 3_000  # 2_000 + 1_000


@pytest.mark.anyio
async def test_withdraw_insufficient_account_balance(db_session: AsyncSession):
    """Withdrawing more than the account holds must raise ValueError."""
    agent = await _make_agent(db_session, balance=5_000)
    account = await _open_checking(db_session, agent, initial=1_000)

    with pytest.raises(ValueError, match="Insufficient account balance"):
        await withdraw(db_session, agent.id, account.id, amount=2_000, tick=1)


# ---------------------------------------------------------------------------
# Loan tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_loan(db_session: AsyncSession):
    """Requesting a loan within credit limits should disburse funds to the wallet."""
    agent = await _make_agent(db_session, balance=1_000, credit_score=700)
    max_allowed = 700 * 100  # 70_000

    loan_account = await request_loan(db_session, agent.id, amount=10_000, tick=1)
    await db_session.flush()

    assert loan_account.account_type == AccountType.LOAN
    assert loan_account.balance == -10_000  # negative = debt
    assert loan_account.is_active is True
    assert agent.balance == 11_000  # 1_000 + 10_000 loan
    # Interest rate should be calculated: max(300, 1000 - (700*7//10)) = max(300, 510) = 510
    assert loan_account.interest_rate == max(300, 1000 - (700 * 7 // 10))


@pytest.mark.anyio
async def test_request_loan_exceeds_credit(db_session: AsyncSession):
    """A loan exceeding the credit limit must be denied."""
    agent = await _make_agent(db_session, balance=1_000, credit_score=100)
    max_allowed = 100 * 100  # 10_000

    with pytest.raises(ValueError, match="Loan denied"):
        await request_loan(db_session, agent.id, amount=max_allowed + 1, tick=1)


@pytest.mark.anyio
async def test_repay_loan_partial(db_session: AsyncSession):
    """Partial loan repayment should reduce the debt but keep the loan active."""
    agent = await _make_agent(db_session, balance=20_000, credit_score=700)

    loan = await request_loan(db_session, agent.id, amount=5_000, tick=1)
    await db_session.flush()
    # agent.balance = 20_000 + 5_000 = 25_000, loan.balance = -5_000

    result = await repay_loan(db_session, agent.id, loan.id, amount=2_000, tick=2)
    await db_session.flush()

    assert result.balance == -3_000  # -5_000 + 2_000
    assert result.is_active is True  # still has debt
    assert agent.balance == 23_000  # 25_000 - 2_000


@pytest.mark.anyio
async def test_repay_loan_full(db_session: AsyncSession):
    """Full loan repayment should close the account and improve credit score."""
    agent = await _make_agent(db_session, balance=20_000, credit_score=700)
    original_credit = agent.credit_score

    loan = await request_loan(db_session, agent.id, amount=5_000, tick=1)
    await db_session.flush()

    result = await repay_loan(db_session, agent.id, loan.id, amount=5_000, tick=2)
    await db_session.flush()

    assert result.balance == 0
    assert result.is_active is False  # closed
    assert agent.credit_score == min(1000, original_credit + 20)


@pytest.mark.anyio
async def test_repay_loan_overpay(db_session: AsyncSession):
    """Overpaying a loan (paying more than owed) should close the account."""
    agent = await _make_agent(db_session, balance=20_000, credit_score=700)

    loan = await request_loan(db_session, agent.id, amount=3_000, tick=1)
    await db_session.flush()

    # Overpay by 1000
    result = await repay_loan(db_session, agent.id, loan.id, amount=4_000, tick=2)
    await db_session.flush()

    assert result.balance == 1_000  # overpaid by 1000 (balance goes positive)
    assert result.is_active is False  # closed because balance >= 0


# ---------------------------------------------------------------------------
# Interest processing tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_process_interest_savings(db_session: AsyncSession):
    """Savings accounts should earn interest proportional to balance * rate."""
    agent = await _make_agent(db_session, balance=100_000)
    savings = await _open_savings(db_session, agent, initial=50_000)
    # Default interest rate = 300 basis points (3%)
    original_balance = savings.balance

    processed = await process_interest(db_session, tick=1)
    await db_session.flush()

    # Interest = 50_000 * 300 / 10_000 = 1_500
    expected_interest = (original_balance * savings.interest_rate) // 10_000
    assert savings.balance == original_balance + expected_interest
    assert processed >= 1


@pytest.mark.anyio
async def test_process_interest_loan(db_session: AsyncSession):
    """Loan accounts should accrue interest, increasing the debt."""
    agent = await _make_agent(db_session, balance=50_000, credit_score=700)

    loan = await request_loan(db_session, agent.id, amount=10_000, tick=1)
    await db_session.flush()
    original_debt = loan.balance  # negative value

    processed = await process_interest(db_session, tick=2)
    await db_session.flush()

    # Interest = abs(-10_000) * rate / 10_000
    expected_interest = (abs(original_debt) * loan.interest_rate) // 10_000
    assert loan.balance == original_debt - expected_interest  # debt increases (more negative)
    assert processed >= 1


@pytest.mark.anyio
async def test_process_interest_checking_no_effect(db_session: AsyncSession):
    """Checking accounts should not earn or be charged interest."""
    agent = await _make_agent(db_session, balance=50_000)
    checking = await _open_checking(db_session, agent, initial=10_000)
    original_balance = checking.balance

    await process_interest(db_session, tick=1)
    await db_session.flush()

    assert checking.balance == original_balance
