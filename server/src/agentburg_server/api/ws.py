"""WebSocket endpoint for agent-server real-time communication."""

import logging
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from agentburg_shared.protocol.messages import (
    AuthenticateMessage,
    ActionMessage,
    AuthResult,
    ClientMessage,
    ErrorMessage,
    MessageType,
    QueryMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Connected agents: agent_id → WebSocket
_connections: dict[UUID, WebSocket] = {}


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
                ErrorMessage(code="AUTH_REQUIRED", message="First message must be authenticate").model_dump(mode="json")
            )
            await websocket.close(code=4001)
            return

        auth_msg = AuthenticateMessage.model_validate(raw)
        # TODO: validate token against DB, get agent_id
        # For now, send auth failure
        await websocket.send_json(
            AuthResult(success=False, message="Auth not yet implemented").model_dump(mode="json")
        )
        await websocket.close(code=4001)

    except WebSocketDisconnect:
        logger.info("Agent %s disconnected", agent_id)
    except ValidationError as e:
        logger.warning("Invalid message format: %s", e)
        await websocket.send_json(
            ErrorMessage(code="INVALID_MESSAGE", message=str(e)).model_dump(mode="json")
        )
        await websocket.close(code=4002)
    finally:
        if agent_id and agent_id in _connections:
            del _connections[agent_id]


async def broadcast_to_agent(agent_id: UUID, data: dict) -> bool:
    """Send a message to a specific connected agent."""
    ws = _connections.get(agent_id)
    if ws is None:
        return False
    await ws.send_json(data)
    return True
