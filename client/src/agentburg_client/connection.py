"""WebSocket connection manager — connects agent brain to world server."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import websockets
from websockets.asyncio.client import ClientConnection

from agentburg_client.config import AgentConfig
from agentburg_shared.protocol.messages import (
    ActionMessage,
    ActionType,
    AuthenticateMessage,
    MessageType,
    QueryMessage,
    QueryType,
)

logger = logging.getLogger(__name__)


class ServerConnection:
    """Manages WebSocket connection to the AgentBurg world server."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.agent_id: UUID | None = None
        self._ws: ClientConnection | None = None
        self._connected = False
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    async def connect(self) -> bool:
        """Connect and authenticate with the server."""
        url = self.config.server.url
        token = self.config.server.token

        for attempt in range(1, self.config.server.max_reconnect_attempts + 1):
            try:
                logger.info("Connecting to %s (attempt %d)...", url, attempt)
                self._ws = await websockets.connect(url)

                # Send authentication
                auth_msg = AuthenticateMessage(agent_token=token)
                await self._ws.send(auth_msg.model_dump_json())

                # Wait for auth result
                raw = await self._ws.recv()
                result = json.loads(raw)

                if result.get("success"):
                    self.agent_id = UUID(result["agent_id"])
                    self._connected = True
                    logger.info("Authenticated as agent %s: %s", self.agent_id, result.get("message"))
                    return True
                else:
                    logger.error("Authentication failed: %s", result.get("message"))
                    await self._ws.close()
                    return False

            except Exception as e:
                logger.warning("Connection attempt %d failed: %s", attempt, e)
                await asyncio.sleep(self.config.server.reconnect_delay)

        logger.error("Failed to connect after %d attempts", self.config.server.max_reconnect_attempts)
        return False

    async def disconnect(self) -> None:
        """Disconnect from server."""
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send_action(self, action: ActionType, params: dict | None = None, request_id: UUID | None = None) -> None:
        """Send an action to the server."""
        if not self._ws:
            raise ConnectionError("Not connected")

        msg = ActionMessage(action=action, params=params or {}, request_id=request_id)
        await self._ws.send(msg.model_dump_json())

    async def send_query(self, query: QueryType, params: dict | None = None, request_id: UUID | None = None) -> None:
        """Send a query to the server."""
        if not self._ws:
            raise ConnectionError("Not connected")

        msg = QueryMessage(query=query, params=params or {}, request_id=request_id)
        await self._ws.send(msg.model_dump_json())

    async def receive(self) -> dict[str, Any]:
        """Receive next message from server."""
        if not self._ws:
            raise ConnectionError("Not connected")

        raw = await self._ws.recv()
        return json.loads(raw)

    async def listen(self) -> None:
        """Listen for messages and put them in the queue."""
        if not self._ws:
            return

        try:
            async for raw in self._ws:
                data = json.loads(raw)
                await self._message_queue.put(data)
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by server")
            self._connected = False

    async def get_message(self, timeout: float = 30.0) -> dict[str, Any] | None:
        """Get next message from queue with timeout."""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
