"""Banking service — deposits, withdrawals, loans, interest, credit scoring."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent
from agentburg_server.models.economy import Account, AccountType
from agentburg_server.models.event import EventCategory
from agentburg_server.services.event_logger import log_event as _log_event

logger = logging.getLogger(__name__)

# Economic limits
MAX_LOAN_AMOUNT = 500_000  # $5,000 in cents
MAX_DEPOSIT_AMOUNT = 10_000_000  # $100,000 in cents
MAX_WITHDRAWAL_AMOUNT = 10_000_000


async def open_account(
    session: AsyncSession,
    agent_id: UUID,
    account_type: AccountType = AccountType.CHECKING,
    initial_deposit: int = 0,
) -> Account:
    """Open a new bank account for an agent."""
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    if account_type != AccountType.LOAN and initial_deposit > 0:
        if agent.balance < initial_deposit:
            raise ValueError("Insufficient balance for initial deposit")
        agent.balance -= initial_deposit

    account = Account(
        agent_id=agent_id,
        account_type=account_type,
        balance=initial_deposit,
    )
    session.add(account)
    return account


async def deposit(
    session: AsyncSession,
    agent_id: UUID,
    account_id: UUID,
    amount: int,
    tick: int,
) -> Account:
    """Deposit funds from agent's wallet into a bank account."""
    if amount <= 0:
        raise ValueError("Deposit amount must be positive")
    if amount > MAX_DEPOSIT_AMOUNT:
        raise ValueError(f"Deposit exceeds limit ({MAX_DEPOSIT_AMOUNT} cents)")

    agent = await session.get(Agent, agent_id)
    account = await session.get(Account, account_id)

    if agent is None:
        raise ValueError("Agent not found")
    if account is None or account.agent_id != agent_id:
        raise ValueError("Account not found or not yours")
    if not account.is_active:
        raise ValueError("Account is closed")
    if agent.balance < amount:
        raise ValueError("Insufficient balance")

    agent.balance -= amount
    account.balance += amount

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.BANK,
        event_type="deposit",
        agent_id=agent_id,
        description=f"Deposited {amount} into account",
        data={"account_id": str(account_id), "amount": amount},
    )

    return account


async def withdraw(
    session: AsyncSession,
    agent_id: UUID,
    account_id: UUID,
    amount: int,
    tick: int,
) -> Account:
    """Withdraw funds from a bank account to agent's wallet."""
    if amount <= 0:
        raise ValueError("Withdrawal amount must be positive")
    if amount > MAX_WITHDRAWAL_AMOUNT:
        raise ValueError(f"Withdrawal exceeds limit ({MAX_WITHDRAWAL_AMOUNT} cents)")

    agent = await session.get(Agent, agent_id)
    account = await session.get(Account, account_id)

    if agent is None:
        raise ValueError("Agent not found")
    if account is None or account.agent_id != agent_id:
        raise ValueError("Account not found or not yours")
    if not account.is_active:
        raise ValueError("Account is closed")
    if account.balance < amount:
        raise ValueError("Insufficient account balance")

    account.balance -= amount
    agent.balance += amount

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.BANK,
        event_type="withdrawal",
        agent_id=agent_id,
        description=f"Withdrew {amount} from account",
        data={"account_id": str(account_id), "amount": amount},
    )

    return account


async def request_loan(
    session: AsyncSession,
    agent_id: UUID,
    amount: int,
    tick: int,
) -> Account:
    """Request a loan based on the agent's credit score."""
    if amount <= 0:
        raise ValueError("Loan amount must be positive")
    if amount > MAX_LOAN_AMOUNT:
        raise ValueError(f"Loan exceeds absolute limit ({MAX_LOAN_AMOUNT} cents)")

    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    # Credit check: max loan = credit_score * 100 (in cents)
    max_loan = agent.credit_score * 100
    if amount > max_loan:
        raise ValueError(f"Loan denied: max allowed is {max_loan} based on credit score {agent.credit_score}")

    # Interest rate based on credit score (higher score = lower rate)
    # 300-1000 basis points depending on credit score
    base_rate = 1000  # 10% for worst credit
    rate_reduction = (agent.credit_score * 7) // 10  # Up to 7% reduction
    interest_rate = max(300, base_rate - rate_reduction)

    loan_account = Account(
        agent_id=agent_id,
        account_type=AccountType.LOAN,
        balance=-amount,  # Negative = debt
        interest_rate=interest_rate,
    )
    session.add(loan_account)

    # Disburse loan to agent's wallet
    agent.balance += amount

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.BANK,
        event_type="loan_issued",
        agent_id=agent_id,
        description=f"Loan of {amount} issued at {interest_rate}bp",
        data={"amount": amount, "interest_rate": interest_rate},
    )

    return loan_account


async def repay_loan(
    session: AsyncSession,
    agent_id: UUID,
    account_id: UUID,
    amount: int,
    tick: int,
) -> Account:
    """Repay a loan (partial or full)."""
    if amount <= 0:
        raise ValueError("Repayment amount must be positive")

    agent = await session.get(Agent, agent_id)
    account = await session.get(Account, account_id)

    if agent is None:
        raise ValueError("Agent not found")
    if account is None or account.agent_id != agent_id:
        raise ValueError("Loan account not found or not yours")
    if account.account_type != AccountType.LOAN:
        raise ValueError("Not a loan account")
    if agent.balance < amount:
        raise ValueError("Insufficient balance for repayment")

    agent.balance -= amount
    account.balance += amount  # Move towards 0

    if account.balance >= 0:
        account.is_active = False
        # Improve credit score on full repayment
        agent.credit_score = min(1000, agent.credit_score + 20)

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.BANK,
        event_type="loan_repayment",
        agent_id=agent_id,
        description=f"Repaid {amount} on loan",
        data={"account_id": str(account_id), "amount": amount, "remaining": account.balance},
    )

    return account


async def process_interest(session: AsyncSession, tick: int) -> int:
    """Process interest for all active accounts. Called each tick cycle (e.g., daily)."""
    stmt = select(Account).where(Account.is_active == True)  # noqa: E712
    result = await session.execute(stmt)
    accounts = list(result.scalars().all())

    processed = 0
    for account in accounts:
        if account.account_type == AccountType.SAVINGS and account.balance > 0:
            # Pay interest on savings
            interest = (account.balance * account.interest_rate) // 10000
            if interest > 0:
                account.balance += interest
                processed += 1

        elif account.account_type == AccountType.LOAN and account.balance < 0:
            # Charge interest on loans
            interest = (abs(account.balance) * account.interest_rate) // 10000
            if interest > 0:
                account.balance -= interest

                # Degrade credit score if loan grows too large
                agent = await session.get(Agent, account.agent_id)
                if agent and abs(account.balance) > agent.credit_score * 200:
                    agent.credit_score = max(0, agent.credit_score - 5)

                processed += 1

    logger.info("Tick %d: processed interest for %d accounts", tick, processed)
    return processed


