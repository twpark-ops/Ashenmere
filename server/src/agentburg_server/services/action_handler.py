"""Action handler — dispatches agent actions to appropriate services."""

import logging
from uuid import UUID

from agentburg_shared.protocol.messages import ActionMessage, ActionResult, ActionType

import agentburg_server.db as _db
from agentburg_server.engine.tick import tick_engine
from agentburg_server.models.economy import OrderSide
from agentburg_server.models.social import CaseType
from agentburg_server.services.bank import deposit, repay_loan, request_loan, withdraw
from agentburg_server.services.business import (
    close_business,
    fire_agent,
    hire_agent,
    set_price,
    start_business,
)
from agentburg_server.services.court import file_lawsuit
from agentburg_server.services.market import place_order
from agentburg_server.services.social import (
    accept_trade_offer,
    build_property,
    create_trade_offer,
    invest_in_business,
    reject_trade_offer,
    send_chat,
)

logger = logging.getLogger(__name__)

# Economic bounds for action parameters
MAX_SALARY = 100_000  # $1,000 per day max
MAX_INVESTMENT = 1_000_000  # $10,000 max single investment
MAX_CHAT_LENGTH = 500

# Map ActionType to OrderSide for market actions
_SIDE_MAP = {
    ActionType.BUY: OrderSide.BUY,
    ActionType.SELL: OrderSide.SELL,
}


async def handle_action(agent_id: UUID, msg: ActionMessage) -> ActionResult:
    """Dispatch an action message to the appropriate service."""
    from agentburg_server.plugins.manager import plugin_manager

    tick = tick_engine.tick

    # Plugin hook: before_action (can override params or block with ValueError)
    try:
        overridden = await plugin_manager.dispatch_before_action(
            agent_id=agent_id,
            action=msg.action,
            params=msg.params,
        )
        if overridden is not None:
            msg = ActionMessage(
                request_id=msg.request_id,
                action=msg.action,
                params=overridden,
            )
    except ValueError as e:
        return ActionResult(
            request_id=msg.request_id,
            success=False,
            action=msg.action,
            message=str(e),
        )

    try:
        async with _db.get_session_factory()() as session:
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

            elif msg.action == ActionType.START_BUSINESS:
                name = msg.params.get("name", "")
                btype = msg.params.get("business_type", "shop")
                location = msg.params.get("location", "")
                if not name:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing business name",
                    )
                biz = await start_business(session, agent_id, name, btype, location, tick)
                await session.commit()
                result_data = {"business_id": str(biz.id), "capital": biz.capital}

            elif msg.action == ActionType.CLOSE_BUSINESS:
                business_id = msg.params.get("business_id")
                if not business_id:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing business_id",
                    )
                biz = await close_business(session, agent_id, UUID(business_id), tick)
                await session.commit()
                result_data = {"business_id": str(biz.id)}

            elif msg.action == ActionType.SET_PRICE:
                business_id = msg.params.get("business_id")
                item = msg.params.get("item", "")
                price = msg.params.get("price", 0)
                if not business_id or not item:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing business_id or item",
                    )
                biz = await set_price(session, agent_id, UUID(business_id), item, price)
                await session.commit()
                result_data = {"products": biz.products}

            elif msg.action == ActionType.HIRE:
                employee_id = msg.params.get("employee_id")
                business_id = msg.params.get("business_id")
                salary = msg.params.get("salary", 100)
                if not employee_id or not business_id:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing employee_id or business_id",
                    )
                if salary <= 0 or salary > MAX_SALARY:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message=f"Salary must be 1-{MAX_SALARY} cents",
                    )
                contract = await hire_agent(
                    session, agent_id, UUID(employee_id), UUID(business_id), salary, tick
                )
                await session.commit()
                result_data = {"contract_id": str(contract.id)}

            elif msg.action == ActionType.FIRE:
                employee_id = msg.params.get("employee_id")
                if not employee_id:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing employee_id",
                    )
                contract = await fire_agent(session, agent_id, UUID(employee_id), tick)
                await session.commit()
                result_data = {"contract_id": str(contract.id)}

            elif msg.action == ActionType.TRADE_OFFER:
                target_id = msg.params.get("target_id")
                offer_items = msg.params.get("offer_items", {})
                request_items = msg.params.get("request_items", {})
                if not target_id or (not offer_items and not request_items):
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing target_id or trade items",
                    )
                contract = await create_trade_offer(
                    session, agent_id, UUID(target_id), offer_items, request_items, tick
                )
                await session.commit()
                result_data = {"offer_id": str(contract.id)}

            elif msg.action == ActionType.ACCEPT_OFFER:
                offer_id = msg.params.get("offer_id")
                if not offer_id:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing offer_id",
                    )
                contract = await accept_trade_offer(session, agent_id, UUID(offer_id), tick)
                await session.commit()
                result_data = {"offer_id": str(contract.id)}

            elif msg.action == ActionType.REJECT_OFFER:
                offer_id = msg.params.get("offer_id")
                if not offer_id:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing offer_id",
                    )
                contract = await reject_trade_offer(session, agent_id, UUID(offer_id), tick)
                await session.commit()
                result_data = {"offer_id": str(contract.id)}

            elif msg.action == ActionType.CHAT:
                target_id = msg.params.get("target_id")
                message = msg.params.get("message", "")
                if not message:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Empty chat message",
                    )
                if len(message) > MAX_CHAT_LENGTH:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message=f"Chat message too long (max {MAX_CHAT_LENGTH} chars)",
                    )
                target_uuid = UUID(target_id) if target_id else None
                event = await send_chat(session, agent_id, target_uuid, message, tick)
                await session.commit()
                result_data = {"event_id": str(event.id)}

            elif msg.action == ActionType.INVEST:
                business_id = msg.params.get("business_id")
                amount = msg.params.get("amount", 0)
                if not business_id or amount <= 0:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing business_id or invalid amount",
                    )
                if amount > MAX_INVESTMENT:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message=f"Investment exceeds limit ({MAX_INVESTMENT} cents)",
                    )
                biz = await invest_in_business(
                    session, agent_id, UUID(business_id), amount, tick
                )
                await session.commit()
                result_data = {"business_id": str(biz.id), "new_capital": biz.capital}

            elif msg.action == ActionType.BUILD:
                name = msg.params.get("name", "")
                ptype = msg.params.get("property_type", "building")
                location = msg.params.get("location", "")
                if not name:
                    return ActionResult(
                        request_id=msg.request_id,
                        success=False,
                        action=msg.action,
                        message="Missing property name",
                    )
                prop = await build_property(session, agent_id, name, ptype, location, tick)
                await session.commit()
                result_data = {"property_id": str(prop.id), "cost": prop.market_value}

            elif msg.action == ActionType.IDLE:
                pass

            else:
                return ActionResult(
                    request_id=msg.request_id,
                    success=False,
                    action=msg.action,
                    message=f"Unknown action: {msg.action}",
                )

            # Plugin hook: after_action (success)
            from agentburg_server.plugins.base import HookType

            await plugin_manager.dispatch(
                HookType.AFTER_ACTION,
                agent_id=agent_id,
                action=msg.action,
                success=True,
                data=result_data,
            )

            return ActionResult(
                request_id=msg.request_id,
                success=True,
                action=msg.action,
                message=f"Action {msg.action} completed",
                data=result_data,
            )

    except ValueError as e:
        # Plugin hook: after_action (failure)
        from agentburg_server.plugins.base import HookType

        await plugin_manager.dispatch(
            HookType.AFTER_ACTION,
            agent_id=agent_id,
            action=msg.action,
            success=False,
            data={"error": str(e)},
        )
        return ActionResult(
            request_id=msg.request_id,
            success=False,
            action=msg.action,
            message=str(e),
        )
    except Exception:
        logger.exception("Unexpected error handling action %s for agent %s", msg.action, agent_id)
        return ActionResult(
            request_id=msg.request_id,
            success=False,
            action=msg.action,
            message="Internal error processing action",
        )
