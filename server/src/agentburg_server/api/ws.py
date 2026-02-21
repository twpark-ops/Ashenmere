"""WebSocket endpoints for agent-server and dashboard communication."""

import contextlib
import logging
import secrets
from hashlib import sha256
from uuid import UUID

from agentburg_shared.protocol.messages import (
    ActionMessage,
    AuthenticateMessage,
    AuthResult,
    ErrorMessage,
    MessageType,
    QueryMessage,
)
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

import agentburg_server.db as _db
from agentburg_server.config import settings
from agentburg_server.models.agent import Agent
from agentburg_server.services.action_handler import handle_action
from agentburg_server.services.query_handler import handle_query
from agentburg_server.services.rate_limiter import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# Connected agents: agent_id → WebSocket
_connections: dict[UUID, WebSocket] = {}

# Connected dashboard viewers: set of WebSocket instances
_dashboard_viewers: set[WebSocket] = set()


async def _authenticate(websocket: WebSocket, raw: dict) -> UUID | None:
    """Validate agent token by hashing it and comparing to stored hash."""
    auth_msg = AuthenticateMessage.model_validate(raw)

    # Hash the raw token to compare against stored hash
    token_hash = sha256(auth_msg.agent_token.encode()).hexdigest()

    async with _db.get_session_factory()() as session:
        stmt = select(Agent).where(Agent.api_token_hash == token_hash)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()

        # Constant-time verification to prevent timing side-channel attacks
        if agent is None or not secrets.compare_digest(agent.api_token_hash, token_hash):
            await websocket.send_json(AuthResult(success=False, message="Invalid agent token").model_dump(mode="json"))
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

        from agentburg_server.metrics import (
            agents_connected,
            ws_auth_failures,
            ws_connections_total,
            ws_messages_received,
        )

        agent_id = await _authenticate(websocket, raw)
        if agent_id is None:
            ws_auth_failures.inc()
            await websocket.close(code=4001)
            return

        ws_connections_total.inc()
        agents_connected.inc()
        _connections[agent_id] = websocket
        logger.info("Agent %s connected", agent_id)

        # Plugin hook: on_agent_connect
        from agentburg_server.plugins.base import HookType
        from agentburg_server.plugins.manager import plugin_manager

        await plugin_manager.dispatch(HookType.ON_AGENT_CONNECT, agent_id=agent_id)

        # Main message loop
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            # Per-agent WebSocket rate limiting
            if not await check_rate_limit(
                key=f"rl:ws:{agent_id}",
                limit=settings.ws_rate_limit_per_second,
                window=1,
            ):
                await websocket.send_json(
                    ErrorMessage(
                        code="RATE_LIMITED",
                        message="Too many messages — slow down",
                    ).model_dump(mode="json")
                )
                continue

            if msg_type == MessageType.ACTION:
                ws_messages_received.labels(message_type="action").inc()
                try:
                    action_msg = ActionMessage.model_validate(raw)
                    result = await handle_action(agent_id, action_msg)
                    await websocket.send_json(result.model_dump(mode="json"))
                except ValueError as e:
                    await websocket.send_json(ErrorMessage(code="ACTION_ERROR", message=str(e)).model_dump(mode="json"))
                except Exception:
                    logger.exception("Error handling action for agent %s", agent_id)
                    await websocket.send_json(
                        ErrorMessage(code="ACTION_ERROR", message="Internal action error").model_dump(mode="json")
                    )

            elif msg_type == MessageType.QUERY:
                ws_messages_received.labels(message_type="query").inc()
                try:
                    query_msg = QueryMessage.model_validate(raw)
                    result = await handle_query(agent_id, query_msg)
                    await websocket.send_json(result.model_dump(mode="json"))
                except ValueError as e:
                    await websocket.send_json(ErrorMessage(code="QUERY_ERROR", message=str(e)).model_dump(mode="json"))
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
        logger.warning("Invalid message format from agent %s: %s", agent_id, e)
        with contextlib.suppress(Exception):
            await websocket.send_json(
                ErrorMessage(
                    code="INVALID_MESSAGE",
                    message="Invalid message format",
                ).model_dump(mode="json")
            )
    except Exception:
        logger.exception("Unexpected error for agent %s", agent_id)
    finally:
        if agent_id:
            from agentburg_server.metrics import agents_connected as _ac

            _ac.dec()
            _connections.pop(agent_id, None)

            # Plugin hook: on_agent_disconnect
            from agentburg_server.plugins.base import HookType
            from agentburg_server.plugins.manager import plugin_manager

            await plugin_manager.dispatch(HookType.ON_AGENT_DISCONNECT, agent_id=agent_id)

            # Mark agent as disconnected
            try:
                async with _db.get_session_factory()() as session:
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


# --- Dashboard WebSocket ---


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    """Dashboard read-only WebSocket for live world updates.

    When ``dashboard_api_key`` is configured, the key must be provided as a
    query parameter (``?key=...``).  If the setting is empty, access is open
    (backwards compatible).
    """
    # Optional API-key gate
    if settings.dashboard_api_key:
        key = websocket.query_params.get("key", "")
        if not secrets.compare_digest(key, settings.dashboard_api_key):
            await websocket.close(code=4003)
            return

    await websocket.accept()
    _dashboard_viewers.add(websocket)
    logger.info("Dashboard viewer connected (total: %d)", len(_dashboard_viewers))

    try:
        # Keep connection alive by reading (and discarding) any incoming messages
        async for _ in websocket.iter_text():
            pass  # Dashboard is read-only; ignore client messages
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Dashboard viewer disconnected unexpectedly")
    finally:
        _dashboard_viewers.discard(websocket)
        logger.info("Dashboard viewer disconnected (remaining: %d)", len(_dashboard_viewers))


async def broadcast_to_dashboard(data: dict) -> None:
    """Send a message to all connected dashboard viewers.

    Silently removes disconnected viewers.
    """
    if not _dashboard_viewers:
        return

    dead: list[WebSocket] = []
    for ws in _dashboard_viewers:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _dashboard_viewers.discard(ws)
