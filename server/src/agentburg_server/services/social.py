"""Social service — trade offers, chat, invest, build."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent
from agentburg_server.models.economy import Property, PropertyType
from agentburg_server.models.event import EventCategory, WorldEventLog
from agentburg_server.models.social import (
    Business,
    Contract,
    ContractStatus,
    ContractType,
)

logger = logging.getLogger(__name__)

# Limits
MAX_TRADE_ITEMS = 10
MAX_TRADE_QUANTITY = 10_000
MAX_INVESTMENT_AMOUNT = 1_000_000  # $10,000 in cents


# --- Trade Offers (Direct P2P) ---


async def create_trade_offer(
    session: AsyncSession,
    offerer_id: UUID,
    target_id: UUID,
    offer_items: dict[str, int],
    request_items: dict[str, int],
    tick: int,
) -> Contract:
    """Create a direct trade offer as a CUSTOM contract.

    Args:
        offer_items: Items and quantities the offerer is giving.
        request_items: Items and quantities the offerer wants in return.
    """
    if offerer_id == target_id:
        raise ValueError("Cannot trade with yourself")

    # Validate trade item counts and quantities
    if len(offer_items) + len(request_items) > MAX_TRADE_ITEMS:
        raise ValueError(f"Trade cannot exceed {MAX_TRADE_ITEMS} total item types")
    for item, qty in {**offer_items, **request_items}.items():
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(f"Invalid quantity for {item}: must be a positive integer")
        if qty > MAX_TRADE_QUANTITY:
            raise ValueError(f"Quantity for {item} exceeds limit ({MAX_TRADE_QUANTITY})")

    offerer = await session.get(Agent, offerer_id)
    if offerer is None:
        raise ValueError("Offerer not found")

    target = await session.get(Agent, target_id)
    if target is None:
        raise ValueError("Target agent not found")

    # Verify offerer has the items
    for item, qty in offer_items.items():
        held = offerer.inventory.get(item, 0)
        if held < qty:
            raise ValueError(f"Insufficient {item}: have {held}, offering {qty}")

    contract = Contract(
        contract_type=ContractType.CUSTOM,
        party_a_id=offerer_id,
        party_b_id=target_id,
        terms={
            "type": "trade_offer",
            "offer": offer_items,
            "request": request_items,
        },
        status=ContractStatus.PROPOSED,
        tick_start=tick,
    )
    session.add(contract)
    await session.flush()

    logger.info("Trade offer from %s to %s: offer=%s, want=%s", offerer_id, target_id, offer_items, request_items)
    return contract


async def accept_trade_offer(
    session: AsyncSession,
    agent_id: UUID,
    offer_id: UUID,
    tick: int,
) -> Contract:
    """Accept a pending trade offer and execute the exchange."""
    contract = await session.get(Contract, offer_id)
    if contract is None:
        raise ValueError("Trade offer not found")
    if contract.party_b_id != agent_id:
        raise ValueError("This offer is not for you")
    if contract.status != ContractStatus.PROPOSED:
        raise ValueError(f"Offer is not pending (status: {contract.status})")

    terms = contract.terms
    if terms.get("type") != "trade_offer":
        raise ValueError("Not a trade offer contract")

    offer_items: dict[str, int] = terms.get("offer", {})
    request_items: dict[str, int] = terms.get("request", {})

    offerer = await session.get(Agent, contract.party_a_id)
    accepter = await session.get(Agent, agent_id)
    if offerer is None or accepter is None:
        raise ValueError("Agent not found")

    # Verify both sides still have the goods
    for item, qty in offer_items.items():
        if offerer.inventory.get(item, 0) < qty:
            raise ValueError(f"Offerer no longer has enough {item}")
    for item, qty in request_items.items():
        if accepter.inventory.get(item, 0) < qty:
            raise ValueError(f"You don't have enough {item}")

    # Execute the swap
    offerer_inv = dict(offerer.inventory)
    accepter_inv = dict(accepter.inventory)

    for item, qty in offer_items.items():
        offerer_inv[item] = offerer_inv.get(item, 0) - qty
        accepter_inv[item] = accepter_inv.get(item, 0) + qty
    for item, qty in request_items.items():
        accepter_inv[item] = accepter_inv.get(item, 0) - qty
        offerer_inv[item] = offerer_inv.get(item, 0) + qty

    # Clean up zero entries
    offerer.inventory = {k: v for k, v in offerer_inv.items() if v > 0}
    accepter.inventory = {k: v for k, v in accepter_inv.items() if v > 0}

    contract.status = ContractStatus.COMPLETED
    contract.tick_end = tick
    await session.flush()

    logger.info("Trade offer %s accepted by %s", offer_id, agent_id)
    return contract


async def reject_trade_offer(
    session: AsyncSession,
    agent_id: UUID,
    offer_id: UUID,
    tick: int,
) -> Contract:
    """Reject a pending trade offer."""
    contract = await session.get(Contract, offer_id)
    if contract is None:
        raise ValueError("Trade offer not found")
    if contract.party_b_id != agent_id:
        raise ValueError("This offer is not for you")
    if contract.status != ContractStatus.PROPOSED:
        raise ValueError("Offer is not pending")

    contract.status = ContractStatus.TERMINATED
    contract.tick_end = tick
    await session.flush()

    logger.info("Trade offer %s rejected by %s", offer_id, agent_id)
    return contract


# --- Chat ---


async def send_chat(
    session: AsyncSession,
    sender_id: UUID,
    target_id: UUID | None,
    message: str,
    tick: int,
) -> WorldEventLog:
    """Record a chat message as a world event.

    If target_id is None, it's a public broadcast.
    """
    sender = await session.get(Agent, sender_id)
    if sender is None:
        raise ValueError("Sender not found")

    if len(message) > 500:
        message = message[:500]

    event = WorldEventLog(
        tick=tick,
        category=EventCategory.SOCIAL,
        event_type="chat",
        agent_id=sender_id,
        target_id=target_id,
        description=f"{sender.name}: {message}",
        data={
            "sender_name": sender.name,
            "message": message,
            "is_public": target_id is None,
        },
    )
    session.add(event)
    await session.flush()
    return event


# --- Invest ---


async def invest_in_business(
    session: AsyncSession,
    investor_id: UUID,
    business_id: UUID,
    amount: int,
    tick: int,
) -> Business:
    """Invest funds into a business (increases its capital)."""
    investor = await session.get(Agent, investor_id)
    if investor is None:
        raise ValueError("Investor not found")
    if amount <= 0:
        raise ValueError("Investment amount must be positive")
    if amount > MAX_INVESTMENT_AMOUNT:
        raise ValueError(f"Investment exceeds limit ({MAX_INVESTMENT_AMOUNT} cents)")
    if investor.balance < amount:
        raise ValueError(f"Insufficient balance: have {investor.balance}, investing {amount}")

    business = await session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found")
    if not business.is_active:
        raise ValueError("Business is closed")

    investor.balance -= amount
    business.capital += amount
    await session.flush()

    logger.info("Agent %s invested %d in business %s", investor_id, amount, business.name)
    return business


# --- Build ---


async def build_property(
    session: AsyncSession,
    agent_id: UUID,
    name: str,
    property_type_str: str,
    location: str,
    tick: int,
) -> Property:
    """Build a new property (costs money)."""
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    try:
        ptype = PropertyType(property_type_str)
    except ValueError as exc:
        raise ValueError(f"Invalid property type: {property_type_str}") from exc

    build_costs: dict[PropertyType, int] = {
        PropertyType.LAND: 2000,
        PropertyType.BUILDING: 10000,
        PropertyType.SHOP: 8000,
        PropertyType.FACTORY: 20000,
        PropertyType.HOUSE: 6000,
    }

    cost = build_costs.get(ptype, 5000)
    if agent.balance < cost:
        raise ValueError(f"Need {cost} cents to build {ptype.value}, have {agent.balance}")

    agent.balance -= cost

    prop = Property(
        name=name,
        property_type=ptype,
        location=location or agent.location,
        owner_id=agent_id,
        market_value=cost,
    )
    session.add(prop)
    await session.flush()

    logger.info("Agent %s built %s (%s) at %s for %d", agent_id, name, ptype.value, location, cost)
    return prop
