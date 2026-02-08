"""Market exchange service — order matching engine using periodic batch auction.

Batch auction processes all pending orders at each tick rather than
continuous matching, which prevents front-running and keeps the game fair.
"""

import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentburg_server.models.agent import Agent
from agentburg_server.models.economy import (
    MarketOrder,
    OrderSide,
    OrderStatus,
    Trade,
)
from agentburg_server.models.event import EventCategory, WorldEventLog

logger = logging.getLogger(__name__)


async def place_order(
    session: AsyncSession,
    agent_id: UUID,
    item: str,
    side: OrderSide,
    price: int,
    quantity: int,
    tick: int,
    tick_expires: int | None = None,
) -> MarketOrder:
    """Place a new market order for an agent."""
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    if price <= 0 or quantity <= 0:
        raise ValueError("Price and quantity must be positive")

    # For buy orders, verify the agent can afford it
    if side == OrderSide.BUY:
        total_cost = price * quantity
        if agent.balance < total_cost:
            raise ValueError(f"Insufficient balance: need {total_cost}, have {agent.balance}")
        # Reserve funds
        agent.balance -= total_cost

    # For sell orders, verify the agent has the item
    if side == OrderSide.SELL:
        current_qty = agent.inventory.get(item, 0)
        if current_qty < quantity:
            raise ValueError(f"Insufficient inventory: need {quantity}, have {current_qty}")
        # Reserve items
        inventory = dict(agent.inventory)
        inventory[item] = current_qty - quantity
        agent.inventory = inventory

    order = MarketOrder(
        agent_id=agent_id,
        item=item,
        side=side,
        price=price,
        quantity=quantity,
        status=OrderStatus.OPEN,
        tick_created=tick,
        tick_expires=tick_expires,
    )
    session.add(order)

    await _log_event(
        session,
        tick=tick,
        category=EventCategory.TRADE,
        event_type=f"order_{side.value}",
        agent_id=agent_id,
        description=f"{side.value} order: {quantity}x {item} @ {price}",
        data={"item": item, "side": side.value, "price": price, "quantity": quantity},
    )

    return order


async def run_batch_auction(session: AsyncSession, tick: int) -> list[Trade]:
    """Execute batch auction for all open orders at this tick.

    Matching algorithm:
    1. Group orders by item
    2. For each item, find crossing orders (buy price >= sell price)
    3. Match at midpoint price (buy_price + sell_price) / 2
    4. Execute trades, update balances and inventories
    """
    trades: list[Trade] = []

    # Expire old orders first
    await _expire_orders(session, tick)

    # Get all open orders grouped by item
    stmt = (
        select(MarketOrder)
        .where(MarketOrder.status == OrderStatus.OPEN)
        .order_by(MarketOrder.item, MarketOrder.price, MarketOrder.tick_created)
    )
    result = await session.execute(stmt)
    orders = list(result.scalars().all())

    # Group by item
    items: dict[str, list[MarketOrder]] = {}
    for order in orders:
        items.setdefault(order.item, []).append(order)

    for item, item_orders in items.items():
        buys = sorted(
            [o for o in item_orders if o.side == OrderSide.BUY],
            key=lambda o: (-o.price, o.tick_created),  # Highest price first, then earliest
        )
        sells = sorted(
            [o for o in item_orders if o.side == OrderSide.SELL],
            key=lambda o: (o.price, o.tick_created),  # Lowest price first, then earliest
        )

        bi, si = 0, 0
        while bi < len(buys) and si < len(sells):
            buy = buys[bi]
            sell = sells[si]

            # Check if orders cross
            if buy.price < sell.price:
                break  # No more matching possible

            # Don't match agent with themselves
            if buy.agent_id == sell.agent_id:
                si += 1
                continue

            # Calculate match
            match_price = (buy.price + sell.price) // 2
            buy_remaining = buy.quantity - buy.filled_quantity
            sell_remaining = sell.quantity - sell.filled_quantity
            match_quantity = min(buy_remaining, sell_remaining)

            if match_quantity <= 0:
                if buy_remaining <= 0:
                    bi += 1
                else:
                    si += 1
                continue

            total = match_price * match_quantity

            # Execute the trade
            trade = Trade(
                tick=tick,
                item=item,
                buyer_id=buy.agent_id,
                seller_id=sell.agent_id,
                price=match_price,
                quantity=match_quantity,
                total=total,
                buy_order_id=buy.id,
                sell_order_id=sell.id,
            )
            session.add(trade)

            # Update order fill quantities
            buy.filled_quantity += match_quantity
            sell.filled_quantity += match_quantity

            if buy.filled_quantity >= buy.quantity:
                buy.status = OrderStatus.FILLED
                bi += 1
            else:
                buy.status = OrderStatus.PARTIALLY_FILLED

            if sell.filled_quantity >= sell.quantity:
                sell.status = OrderStatus.FILLED
                si += 1
            else:
                sell.status = OrderStatus.PARTIALLY_FILLED

            # Update agent balances and inventories
            buyer = await session.get(Agent, buy.agent_id)
            seller = await session.get(Agent, sell.agent_id)

            if buyer and seller:
                # Buyer: refund price difference (reserved at buy.price, paid match_price)
                price_diff = (buy.price - match_price) * match_quantity
                buyer.balance += price_diff

                # Buyer: receive items
                inv = dict(buyer.inventory)
                inv[item] = inv.get(item, 0) + match_quantity
                buyer.inventory = inv
                buyer.total_trades += 1

                # Seller: receive payment
                seller.balance += total
                seller.total_trades += 1
                seller.total_earnings += total

            trades.append(trade)

            # Log the trade event
            await _log_event(
                session,
                tick=tick,
                category=EventCategory.TRADE,
                event_type="trade_executed",
                agent_id=buy.agent_id,
                target_id=sell.agent_id,
                description=f"Trade: {match_quantity}x {item} @ {match_price}",
                data={
                    "item": item,
                    "price": match_price,
                    "quantity": match_quantity,
                    "total": total,
                    "buyer_id": str(buy.agent_id),
                    "seller_id": str(sell.agent_id),
                },
            )

    logger.info("Tick %d: executed %d trades across %d items", tick, len(trades), len(items))
    return trades


async def cancel_order(
    session: AsyncSession, order_id: UUID, agent_id: UUID, tick: int
) -> MarketOrder:
    """Cancel an open order and refund reserved funds/items."""
    order = await session.get(MarketOrder, order_id)
    if order is None:
        raise ValueError("Order not found")
    if order.agent_id != agent_id:
        raise ValueError("Not your order")
    if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
        raise ValueError("Order is not cancellable")

    remaining = order.quantity - order.filled_quantity
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    if order.side == OrderSide.BUY:
        # Refund reserved funds
        agent.balance += order.price * remaining
    elif order.side == OrderSide.SELL:
        # Return reserved items
        inv = dict(agent.inventory)
        inv[order.item] = inv.get(order.item, 0) + remaining
        agent.inventory = inv

    order.status = OrderStatus.CANCELLED
    return order


async def get_market_prices(session: AsyncSession) -> dict[str, int]:
    """Get latest trade prices for all items (VWAP of last 10 trades per item)."""
    from sqlalchemy import desc, func

    # Get distinct items that have been traded
    items_stmt = select(Trade.item).distinct()
    items_result = await session.execute(items_stmt)
    items = [row[0] for row in items_result]

    prices: dict[str, int] = {}
    for item in items:
        stmt = (
            select(
                func.sum(Trade.total).label("total_value"),
                func.sum(Trade.quantity).label("total_qty"),
            )
            .where(Trade.item == item)
            .order_by(desc(Trade.tick))
            .limit(10)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row and row.total_qty and row.total_qty > 0:
            prices[item] = row.total_value // row.total_qty

    return prices


async def _expire_orders(session: AsyncSession, tick: int) -> int:
    """Expire orders that have passed their expiry tick."""
    stmt = (
        update(MarketOrder)
        .where(
            MarketOrder.status.in_([OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]),
            MarketOrder.tick_expires.is_not(None),
            MarketOrder.tick_expires <= tick,
        )
        .values(status=OrderStatus.EXPIRED)
    )
    result = await session.execute(stmt)
    # TODO: refund reserved funds for expired orders
    return result.rowcount


async def _log_event(
    session: AsyncSession,
    tick: int,
    category: EventCategory,
    event_type: str,
    description: str,
    agent_id: UUID | None = None,
    target_id: UUID | None = None,
    data: dict | None = None,
) -> None:
    """Helper to create an event log entry."""
    event = WorldEventLog(
        tick=tick,
        category=category,
        event_type=event_type,
        agent_id=agent_id,
        target_id=target_id,
        description=description,
        data=data or {},
    )
    session.add(event)
