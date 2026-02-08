"""WebSocket endpoint for agent-server real-time communication."""

import logging
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from agentburg_shared.protocol.messages import (
    ActionMessage,
    ActionResult,
    AuthenticateMessage,
    AuthResult,
    ErrorMessage,
    MessageType,
    QueryMessage,
)
from agentburg_server.db import async_session_factory
from agentburg_server.models.agent import Agent
from agentburg_server.services.action_handler import handle_action
from agentburg_server.services.query_handler import handle_query

logger = logging.getLogger(__name__)

router = APIRouter()

# Connected agents: agent_id → WebSocket
_connections: dict[UUID, WebSocket] = {}


async def _authenticate(websocket: WebSocket, raw: dict) -> UUID | None:
    """Validate agent token by hashing it and comparing to stored hash."""
    auth_msg = AuthenticateMessage.model_validate(raw)

    # Hash the raw token to compare against stored hash
    token_hash = sha256(auth_msg.agent_token.encode()).hexdigest()

    async with async_session_factory() as session:
        stmt = select(Agent).where(Agent.api_token_hash == token_hash)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()

        if agent is None:
            await websocket.send_json(
                AuthResult(success=False, message="Invalid agent token").model_dump(mode="json")
            )
            return None

        agent.is_connected = True
        await session.commit()

        await websocket.send_json(
            AuthResult(
                success=True,
                agent_id=agent.id,
                message=f"Welcome, {agent.name}!",
            ).model_dump(mode="json")
        )
        return agent.id


@router.websocket("/ws")
async def agent_websocket(websocket: WebSocket) -> None:
    """Main WebSocket endpoint for agent communication."""
    await websocket.accept()
    agent_id: UUID | None = None

    try:
        # First message must be authentication
        raw = await websocket.receive_json()
        msg_type = raw.get("type")

        if msg_type != MessageType.AUTHENTICATE:
            await websocket.send_json(
                ErrorMessage(
                    code="AUTH_REQUIRED",
                    message="First message must be authenticate",
                ).model_dump(mode="json")
            )
            await websocket.close(code=4001)
            return

        agent_id = await _authenticate(websocket, raw)
        if agent_id is None:
            await websocket.close(code=4001)
            return

        _connections[agent_id] = websocket
        logger.info("Agent %s connected", agent_id)

        # Main message loop
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            if msg_type == MessageType.ACTION:
                try:
                    action_msg = ActionMessage.model_validate(raw)
                    result = await handle_action(agent_id, action_msg)
                    await websocket.send_json(result.model_dump(mode="json"))
                except ValueError as e:
                    await websocket.send_json(
                        ErrorMessage(code="ACTION_ERROR", message=str(e)).model_dump(mode="json")
                    )
                except Exception:
                    logger.exception("Error handling action for agent %s", agent_id)
                    await websocket.send_json(
                        ErrorMessage(code="ACTION_ERROR", message="Internal action error").model_dump(mode="json")
                    )

            elif msg_type == MessageType.QUERY:
                try:
                    query_msg = QueryMessage.model_validate(raw)
                    result = await handle_query(agent_id, query_msg)
                    await websocket.send_json(result.model_dump(mode="json"))
                except ValueError as e:
                    await websocket.send_json(
                        ErrorMessage(code="QUERY_ERROR", message=str(e)).model_dump(mode="json")
                    )
                except Exception:
                    logger.exception("Error handling query for agent %s", agent_id)
                    await websocket.send_json(
                        ErrorMessage(code="QUERY_ERROR", message="Internal query error").model_dump(mode="json")
                    )

            else:
                await websocket.send_json(
                    ErrorMessage(
                        code="UNKNOWN_MESSAGE",
                        message=f"Unknown message type: {msg_type}",
                    ).model_dump(mode="json")
                )

    except WebSocketDisconnect:
        logger.info("Agent %s disconnected", agent_id)
    except ValidationError as e:
        logger.warning("Invalid message format: %s", e)
        try:
            await websocket.send_json(
                ErrorMessage(code="INVALID_MESSAGE", message=str(e)).model_dump(mode="json")
            )
        except Exception:
            pass
    except Exception:
        logger.exception("Unexpected error for agent %s", agent_id)
    finally:
        if agent_id:
            _connections.pop(agent_id, None)
            # Mark agent as disconnected
            try:
                async with async_session_factory() as session:
                    agent = await session.get(Agent, agent_id)
                    if agent:
                        agent.is_connected = False
                        await session.commit()
            except Exception:
                logger.exception("Failed to update disconnect state for %s", agent_id)


async def broadcast_to_agent(agent_id: UUID, data: dict) -> bool:
    """Send a message to a specific connected agent."""
    ws = _connections.get(agent_id)
    if ws is None:
        return False
    try:
        await ws.send_json(data)
        return True
    except Exception:
        _connections.pop(agent_id, None)
        return False


def get_connected_agents() -> list[UUID]:
    """Return list of currently connected agent IDs."""
    return list(_connections.keys())
