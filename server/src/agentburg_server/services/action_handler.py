"""Action handler — dispatches agent actions to appropriate services."""

import logging
from uuid import UUID

from agentburg_shared.protocol.messages import ActionMessage, ActionResult, ActionType
from agentburg_server.db import async_session_factory
from agentburg_server.engine.tick import tick_engine
from agentburg_server.models.economy import OrderSide
from agentburg_server.models.social import CaseType
from agentburg_server.services.market import place_order, cancel_order
from agentburg_server.services.bank import deposit, withdraw, request_loan, repay_loan
from agentburg_server.services.court import file_lawsuit

logger = logging.getLogger(__name__)

# Map ActionType to OrderSide for market actions
_SIDE_MAP = {
    ActionType.BUY: OrderSide.BUY,
    ActionType.SELL: OrderSide.SELL,
}


async def handle_action(agent_id: UUID, msg: ActionMessage) -> ActionResult:
    """Dispatch an action message to the appropriate service."""
    tick = tick_engine.tick

    try:
        async with async_session_factory() as session:
            result_data: dict = {}

            if msg.action in (ActionType.BUY, ActionType.SELL):
                item = msg.params.get("item", "")
                price = msg.params.get("price", 0)
                quantity = msg.params.get("quantity", 1)
                if not item or price <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing item or invalid price",
                    )

                order = await place_order(
                    session,
                    agent_id=agent_id,
                    item=item,
                    side=_SIDE_MAP[msg.action],
                    price=price,
                    quantity=quantity,
                    tick=tick,
                )
                await session.commit()
                result_data = {"order_id": str(order.id)}

            elif msg.action == ActionType.DEPOSIT:
                amount = msg.params.get("amount", 0)
                account_id = msg.params.get("account_id")
                if not account_id or amount <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing account_id or invalid amount",
                    )
                account = await deposit(session, agent_id, UUID(account_id), amount, tick)
                await session.commit()
                result_data = {"new_balance": account.balance}

            elif msg.action == ActionType.WITHDRAW:
                amount = msg.params.get("amount", 0)
                account_id = msg.params.get("account_id")
                if not account_id or amount <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing account_id or invalid amount",
                    )
                account = await withdraw(session, agent_id, UUID(account_id), amount, tick)
                await session.commit()
                result_data = {"new_balance": account.balance}

            elif msg.action == ActionType.BORROW:
                amount = msg.params.get("amount", 0)
                if amount <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Invalid loan amount",
                    )
                loan = await request_loan(session, agent_id, amount, tick)
                await session.commit()
                result_data = {"loan_account_id": str(loan.id), "interest_rate": loan.interest_rate}

            elif msg.action == ActionType.REPAY:
                amount = msg.params.get("amount", 0)
                account_id = msg.params.get("account_id")
                if not account_id or amount <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing account_id or invalid amount",
                    )
                account = await repay_loan(session, agent_id, UUID(account_id), amount, tick)
                await session.commit()
                result_data = {"remaining_debt": abs(account.balance)}

            elif msg.action == ActionType.SUE:
                target_id = msg.params.get("target_id")
                case_type_str = msg.params.get("case_type", "other")
                description = msg.params.get("description", "")
                evidence = msg.params.get("evidence", {})
                if not target_id or not description:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing target_id or description",
                    )
                case_type = CaseType(case_type_str)
                case = await file_lawsuit(
                    session, agent_id, UUID(target_id), case_type, description, evidence, tick
                )
                await session.commit()
                result_data = {"case_id": str(case.id)}

            elif msg.action == ActionType.IDLE:
                # Agent chose to do nothing this tick
                pass

            else:
                return ActionResult(
                    request_id=msg.request_id,
                    success=False,
                    action=msg.action,
                    message=f"Action not yet implemented: {msg.action}",
                )

            return ActionResult(
                request_id=msg.request_id,
                success=True,
                action=msg.action,
                message=f"Action {msg.action} completed",
                data=result_data,
            )

    except ValueError as e:
        return ActionResult(
            request_id=msg.request_id,
            success=False,
            action=msg.action,
            message=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error handling action %s for agent %s", msg.action, agent_id)
        return ActionResult(
            request_id=msg.request_id,
            success=False,
            action=msg.action,
            message=f"Internal error: {type(e).__name__}",
        )
