"""Business service — start, close, hire, fire, set prices."""

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent
from agentburg_server.models.social import (
    Business,
    BusinessType,
    Contract,
    ContractStatus,
    ContractType,
)

logger = logging.getLogger(__name__)

# Limits
MAX_BUSINESSES_PER_AGENT = 5
MAX_EMPLOYEES_PER_BUSINESS = 20
MAX_SALARY = 100_000  # $1,000 per day in cents
MAX_PRODUCTS_PER_BUSINESS = 50

# Startup cost per business type (in cents)
_STARTUP_COSTS: dict[BusinessType, int] = {
    BusinessType.SHOP: 5000,
    BusinessType.FACTORY: 15000,
    BusinessType.FARM: 8000,
    BusinessType.BANK: 50000,
    BusinessType.RESTAURANT: 7000,
    BusinessType.SERVICE: 3000,
    BusinessType.CUSTOM: 5000,
}


async def start_business(
    session: AsyncSession,
    agent_id: UUID,
    name: str,
    business_type_str: str,
    location: str,
    tick: int,
) -> Business:
    """Create a new business owned by the agent."""
    if not name or len(name) > 100:
        raise ValueError("Business name must be 1-100 characters")

    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    # Check business ownership limit
    existing_count = await session.scalar(
        select(func.count()).select_from(Business).where(
            Business.owner_id == agent_id,
            Business.is_active == True,  # noqa: E712
        )
    )
    if (existing_count or 0) >= MAX_BUSINESSES_PER_AGENT:
        raise ValueError(f"Cannot own more than {MAX_BUSINESSES_PER_AGENT} active businesses")

    try:
        btype = BusinessType(business_type_str)
    except ValueError as exc:
        raise ValueError(f"Invalid business type: {business_type_str}") from exc

    startup_cost = _STARTUP_COSTS.get(btype, 5000)
    if agent.balance < startup_cost:
        raise ValueError(f"Need {startup_cost} cents to start a {btype.value}, have {agent.balance}")

    agent.balance -= startup_cost

    business = Business(
        name=name,
        business_type=btype,
        owner_id=agent_id,
        location=location or agent.location,
        capital=startup_cost,
    )
    session.add(business)
    await session.flush()

    logger.info("Agent %s started business %s (%s)", agent_id, name, btype.value)
    return business


async def close_business(
    session: AsyncSession,
    agent_id: UUID,
    business_id: UUID,
    tick: int,
) -> Business:
    """Close a business. Returns remaining capital to the owner."""
    business = await session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found")
    if business.owner_id != agent_id:
        raise ValueError("You don't own this business")
    if not business.is_active:
        raise ValueError("Business is already closed")

    # Liquidate: return 50% of remaining capital
    refund = business.capital // 2
    agent = await session.get(Agent, agent_id)
    if agent:
        agent.balance += refund

    business.is_active = False
    business.capital = 0
    await session.flush()

    logger.info("Agent %s closed business %s, refund=%d", agent_id, business.name, refund)
    return business


async def set_price(
    session: AsyncSession,
    agent_id: UUID,
    business_id: UUID,
    item: str,
    price: int,
) -> Business:
    """Set or update the price of a product in a business."""
    business = await session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found")
    if business.owner_id != agent_id:
        raise ValueError("You don't own this business")
    if not business.is_active:
        raise ValueError("Business is closed")
    if price < 0:
        raise ValueError("Price cannot be negative")
    if not item or len(item) > 100:
        raise ValueError("Product name must be 1-100 characters")

    products = dict(business.products)
    if price == 0:
        products.pop(item, None)  # Remove item from catalog
    else:
        if item not in products and len(products) >= MAX_PRODUCTS_PER_BUSINESS:
            raise ValueError(f"Business has max {MAX_PRODUCTS_PER_BUSINESS} products")
        products[item] = price
    business.products = products
    await session.flush()
    return business


async def hire_agent(
    session: AsyncSession,
    employer_id: UUID,
    employee_id: UUID,
    business_id: UUID,
    salary: int,
    tick: int,
) -> Contract:
    """Hire an agent as an employee via an employment contract."""
    if employer_id == employee_id:
        raise ValueError("Cannot hire yourself")
    if salary <= 0 or salary > MAX_SALARY:
        raise ValueError(f"Salary must be between 1 and {MAX_SALARY} cents")

    business = await session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found")
    if business.owner_id != employer_id:
        raise ValueError("You don't own this business")
    if not business.is_active:
        raise ValueError("Business is closed")
    if business.employees >= MAX_EMPLOYEES_PER_BUSINESS:
        raise ValueError(f"Business has max {MAX_EMPLOYEES_PER_BUSINESS} employees")

    employee = await session.get(Agent, employee_id)
    if employee is None:
        raise ValueError("Employee agent not found")

    # Check for existing employment at this business
    existing = await session.execute(
        select(Contract).where(
            Contract.party_a_id == employer_id,
            Contract.party_b_id == employee_id,
            Contract.contract_type == ContractType.EMPLOYMENT,
            Contract.status == ContractStatus.ACTIVE,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("Agent is already employed by you")

    contract = Contract(
        contract_type=ContractType.EMPLOYMENT,
        party_a_id=employer_id,
        party_b_id=employee_id,
        terms={"business_id": str(business_id), "role": "employee"},
        payment_amount=salary,
        payment_interval_ticks=720,  # Pay once per simulated day
        tick_start=tick,
        status=ContractStatus.ACTIVE,
    )
    session.add(contract)

    business.employees += 1
    await session.flush()

    logger.info(
        "Agent %s hired %s at business %s, salary=%d/day",
        employer_id, employee_id, business.name, salary,
    )
    return contract


async def fire_agent(
    session: AsyncSession,
    employer_id: UUID,
    employee_id: UUID,
    tick: int,
) -> Contract:
    """Terminate an employment contract."""
    result = await session.execute(
        select(Contract).where(
            Contract.party_a_id == employer_id,
            Contract.party_b_id == employee_id,
            Contract.contract_type == ContractType.EMPLOYMENT,
            Contract.status == ContractStatus.ACTIVE,
        )
    )
    contract = result.scalar_one_or_none()
    if contract is None:
        raise ValueError("No active employment contract found")

    contract.status = ContractStatus.TERMINATED
    contract.tick_end = tick

    # Update business employee count
    business_id_str = contract.terms.get("business_id")
    if business_id_str:
        business = await session.get(Business, UUID(business_id_str))
        if business and business.employees > 0:
            business.employees -= 1

    await session.flush()

    logger.info("Agent %s fired %s", employer_id, employee_id)
    return contract
