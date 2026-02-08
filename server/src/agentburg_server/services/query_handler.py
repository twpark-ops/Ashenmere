"""Query handler — dispatches agent queries to appropriate data sources."""

import logging
from uuid import UUID

from agentburg_shared.protocol.messages import QueryMessage, QueryResult, QueryType
from sqlalchemy import func, select

import agentburg_server.db as _db
from agentburg_server.models.agent import Agent, AgentStatus
from agentburg_server.models.economy import (
    MarketOrder,
    OrderStatus,
    Property,
    Trade,
)
from agentburg_server.models.social import Business, CaseStatus, CourtCase
from agentburg_server.services.market import get_market_prices

logger = logging.getLogger(__name__)


async def handle_query(agent_id: UUID, msg: QueryMessage) -> QueryResult:
    """Dispatch a query message and return results."""
    try:
        async with _db.get_session_factory()() as session:
            data: dict = {}

            if msg.query == QueryType.MY_BALANCE:
                agent = await session.get(Agent, agent_id)
                if agent:
                    data = {
                        "balance": agent.balance,
                        "total_earnings": agent.total_earnings,
                        "total_losses": agent.total_losses,
                    }

            elif msg.query == QueryType.MY_INVENTORY:
                agent = await session.get(Agent, agent_id)
                if agent:
                    data = {"inventory": agent.inventory}

            elif msg.query == QueryType.MY_PROPERTIES:
                stmt = select(Property).where(Property.owner_id == agent_id)
                result = await session.execute(stmt)
                properties = result.scalars().all()
                data = {
                    "properties": [
                        {
                            "id": str(p.id),
                            "name": p.name,
                            "type": p.property_type.value,
                            "location": p.location,
                            "market_value": p.market_value,
                        }
                        for p in properties
                    ]
                }

            elif msg.query == QueryType.MARKET_PRICES:
                prices = await get_market_prices(session)
                data = {"prices": prices}

            elif msg.query == QueryType.MARKET_ORDERS:
                item = msg.params.get("item")
                stmt = select(MarketOrder).where(
                    MarketOrder.status == OrderStatus.OPEN,
                )
                if item:
                    stmt = stmt.where(MarketOrder.item == item)
                stmt = stmt.limit(50)
                result = await session.execute(stmt)
                orders = result.scalars().all()
                data = {
                    "orders": [
                        {
                            "id": str(o.id),
                            "item": o.item,
                            "side": o.side.value,
                            "price": o.price,
                            "quantity": o.quantity - o.filled_quantity,
                        }
                        for o in orders
                    ]
                }

            elif msg.query == QueryType.AGENT_INFO:
                target_id = msg.params.get("agent_id")
                if target_id:
                    target = await session.get(Agent, UUID(target_id))
                    if target:
                        data = {
                            "name": target.name,
                            "title": target.title,
                            "reputation": target.reputation,
                            "total_trades": target.total_trades,
                            "status": target.status.value,
                            "location": target.location,
                        }

            elif msg.query == QueryType.BANK_RATES:
                # Return current bank interest rates
                data = {
                    "checking_rate": 0,
                    "savings_rate": 300,  # 3% in basis points
                    "loan_base_rate": 1000,  # 10% base
                }

            elif msg.query == QueryType.COURT_CASES:
                # Get cases involving this agent
                stmt = select(CourtCase).where(
                    (CourtCase.plaintiff_id == agent_id) | (CourtCase.defendant_id == agent_id),
                    CourtCase.status.in_([CaseStatus.FILED, CaseStatus.IN_PROGRESS]),
                )
                result = await session.execute(stmt)
                cases = result.scalars().all()
                data = {
                    "cases": [
                        {
                            "id": str(c.id),
                            "type": c.case_type.value,
                            "status": c.status.value,
                            "plaintiff_id": str(c.plaintiff_id),
                            "defendant_id": str(c.defendant_id),
                            "description": c.description[:200],
                        }
                        for c in cases
                    ]
                }

            elif msg.query == QueryType.BUSINESS_LIST:
                stmt = select(Business).where(Business.is_active == True).limit(50)  # noqa: E712
                result = await session.execute(stmt)
                businesses = result.scalars().all()
                data = {
                    "businesses": [
                        {
                            "id": str(b.id),
                            "name": b.name,
                            "type": b.business_type.value,
                            "owner_id": str(b.owner_id),
                            "location": b.location,
                            "products": b.products,
                        }
                        for b in businesses
                    ]
                }

            elif msg.query == QueryType.WORLD_STATUS:
                total = await session.scalar(select(func.count()).select_from(Agent))
                active = await session.scalar(
                    select(func.count())
                    .select_from(Agent)
                    .where(Agent.status == AgentStatus.ACTIVE)
                )
                trades = await session.scalar(select(func.count()).select_from(Trade))
                data = {
                    "total_agents": total or 0,
                    "active_agents": active or 0,
                    "total_trades": trades or 0,
                }

            else:
                return QueryResult(
                    request_id=msg.request_id,
                    query=msg.query,
                    data={"error": f"Unknown query type: {msg.query}"},
                )

            return QueryResult(
                request_id=msg.request_id,
                query=msg.query,
                data=data,
            )

    except ValueError:
        return QueryResult(
            request_id=msg.request_id,
            query=msg.query,
            data={"error": "Invalid query parameters"},
        )
    except Exception:
        logger.exception("Error handling query %s for agent %s", msg.query, agent_id)
        return QueryResult(
            request_id=msg.request_id,
            query=msg.query,
            data={"error": "Internal query error"},
        )
