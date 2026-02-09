"""WebSocket connection manager — connects agent brain to world server.

Includes exponential backoff with jitter for reconnection, ping-pong heartbeat
for stale connection detection, and a connection state machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

from agentburg_shared.protocol.messages import (
    ActionMessage,
    ActionType,
    AuthenticateMessage,
    QueryMessage,
    QueryType,
)

from agentburg_client.config import AgentConfig

logger = logging.getLogger(__name__)


class ConnectionState(StrEnum):
    """WebSocket connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    SHUTTING_DOWN = "shutting_down"


class ServerConnection:
    """Manages WebSocket connection to the AgentBurg world server.

    Features:
        - Exponential backoff with jitter for reconnection (base 1s, max 60s)
        - Ping-pong heartbeat every N seconds to detect stale connections
        - Connection state machine for clear lifecycle management
        - Graceful shutdown support
        - Configurable max reconnect attempts (0 = unlimited)
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.agent_id: UUID | None = None
        self._ws: ClientConnection | None = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        self._reconnect_count: int = 0

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def connected(self) -> bool:
        """Whether the connection is fully authenticated and ready."""
        return self._state == ConnectionState.CONNECTED and self._ws is not None

    def request_shutdown(self) -> None:
        """Signal that we want to shut down gracefully.

        Can be called from signal handlers or any context.
        """
        logger.info("Shutdown requested")
        self._state = ConnectionState.SHUTTING_DOWN
        self._shutdown_event.set()

    async def connect(self) -> bool:
        """Connect and authenticate with the server using exponential backoff.

        Returns True on successful connection + authentication.
        Returns False if max attempts exceeded or shutdown requested.
        """
        url = self.config.server.url
        token = self.config.server.token
        base_delay = self.config.server.reconnect_delay
        max_delay = self.config.server.max_reconnect_delay
        max_attempts = self.config.server.max_reconnect_attempts

        attempt = 0
        while not self._shutdown_event.is_set():
            attempt += 1
            if max_attempts > 0 and attempt > max_attempts:
                logger.error(
                    "Failed to connect after %d attempts (max: %d)",
                    attempt - 1,
                    max_attempts,
                )
                return False

            self._state = ConnectionState.CONNECTING
            try:
                logger.info(
                    "Connecting to %s (attempt %d%s)...",
                    url,
                    attempt,
                    f"/{max_attempts}" if max_attempts > 0 else "",
                )
                self._ws = await websockets.connect(
                    url,
                    ping_interval=None,  # We handle heartbeats ourselves
                    close_timeout=10,
                )

                # Authenticate
                self._state = ConnectionState.AUTHENTICATING
                auth_msg = AuthenticateMessage(agent_token=token)
                await self._ws.send(auth_msg.model_dump_json())

                # Wait for auth result with a timeout
                raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
                result = json.loads(raw)

                if result.get("success"):
                    self.agent_id = UUID(result["agent_id"])
                    self._state = ConnectionState.CONNECTED
                    self._reconnect_count = 0
                    logger.info(
                        "Authenticated as agent %s: %s",
                        self.agent_id,
                        result.get("message"),
                    )
                    # Start heartbeat
                    self._start_heartbeat()
                    return True
                else:
                    logger.error("Authentication failed: %s", result.get("message"))
                    await self._close_ws()
                    # Authentication failures are not transient — don't retry
                    self._state = ConnectionState.DISCONNECTED
                    return False

            except TimeoutError:
                logger.warning(
                    "Connection attempt %d timed out during %s",
                    attempt,
                    self._state.value,
                )
                await self._close_ws()

            except InvalidURI as e:
                logger.error("Invalid server URL '%s': %s", url, e)
                self._state = ConnectionState.DISCONNECTED
                return False

            except Exception as e:
                logger.warning("Connection attempt %d failed: %s", attempt, e)
                await self._close_ws()

            # Exponential backoff with full jitter
            delay = _backoff_delay(attempt, base_delay, max_delay)
            logger.debug("Waiting %.1fs before next connection attempt...", delay)

            # Wait for either the backoff delay or a shutdown signal
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
                # shutdown_event was set during backoff
                logger.info("Shutdown requested during reconnect backoff")
                self._state = ConnectionState.SHUTTING_DOWN
                return False
            except TimeoutError:
                # Normal backoff timeout expired, continue to next attempt
                pass

        logger.info("Connect loop exited due to shutdown")
        self._state = ConnectionState.SHUTTING_DOWN
        return False

    async def reconnect(self) -> bool:
        """Attempt to reconnect after a connection loss.

        Uses the same exponential backoff as connect().
        Returns True on successful reconnection.
        """
        if self._state == ConnectionState.SHUTTING_DOWN:
            return False

        self._reconnect_count += 1
        self._stop_heartbeat()
        self._state = ConnectionState.RECONNECTING
        logger.info(
            "Reconnecting (attempt #%d total reconnections)...",
            self._reconnect_count,
        )
        return await self.connect()

    async def disconnect(self) -> None:
        """Disconnect from server and clean up resources."""
        self._state = ConnectionState.SHUTTING_DOWN
        self._shutdown_event.set()
        self._stop_heartbeat()
        await self._close_ws()
        self._state = ConnectionState.DISCONNECTED
        logger.info("Disconnected from server")

    async def send_action(
        self,
        action: ActionType,
        params: dict | None = None,
        request_id: UUID | None = None,
    ) -> None:
        """Send an action to the server."""
        if not self._ws or self._state != ConnectionState.CONNECTED:
            raise ConnectionError(f"Not connected (state={self._state.value})")

        msg = ActionMessage(action=action, params=params or {}, request_id=request_id)
        await self._ws.send(msg.model_dump_json())

    async def send_query(
        self,
        query: QueryType,
        params: dict | None = None,
        request_id: UUID | None = None,
    ) -> None:
        """Send a query to the server."""
        if not self._ws or self._state != ConnectionState.CONNECTED:
            raise ConnectionError(f"Not connected (state={self._state.value})")

        msg = QueryMessage(query=query, params=params or {}, request_id=request_id)
        await self._ws.send(msg.model_dump_json())

    async def receive(self) -> dict[str, Any]:
        """Receive next message from server (low-level, blocking)."""
        if not self._ws:
            raise ConnectionError("Not connected")

        raw = await self._ws.recv()
        return json.loads(raw)

    async def listen(self) -> None:
        """Listen for messages and put them in the queue.

        Runs until the connection is closed or shutdown is requested.
        Sets state to DISCONNECTED on connection loss (not SHUTTING_DOWN).
        """
        if not self._ws:
            return

        try:
            async for raw in self._ws:
                if self._shutdown_event.is_set():
                    break
                try:
                    data = json.loads(raw)
                    await self._message_queue.put(data)
                except json.JSONDecodeError:
                    logger.warning("Received non-JSON message, ignoring: %s", raw[:100])
        except ConnectionClosed as e:
            if self._state != ConnectionState.SHUTTING_DOWN:
                logger.warning(
                    "Connection closed by server (code=%s, reason=%s)",
                    e.code,
                    e.reason,
                )
                self._state = ConnectionState.DISCONNECTED
        except Exception as e:
            if self._state != ConnectionState.SHUTTING_DOWN:
                logger.error("Unexpected error in listener: %s", e)
                self._state = ConnectionState.DISCONNECTED

    async def get_message(self, timeout: float = 30.0) -> dict[str, Any] | None:
        """Get next message from queue with timeout."""
        try:
            return await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    # --- Heartbeat ---

    def _start_heartbeat(self) -> None:
        """Start the ping-pong heartbeat task."""
        self._stop_heartbeat()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="heartbeat",
        )

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat task if running."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Send periodic pings and detect stale connections.

        If a ping fails or times out, the connection is marked as dead.
        """
        interval = self.config.server.heartbeat_interval
        logger.debug("Heartbeat started (interval=%.0fs)", interval)
        try:
            while self._state == ConnectionState.CONNECTED and self._ws is not None:
                await asyncio.sleep(interval)
                if self._state != ConnectionState.CONNECTED or self._ws is None:
                    break
                try:
                    pong = await self._ws.ping()
                    # Wait for pong with a generous timeout
                    await asyncio.wait_for(pong, timeout=10.0)
                    logger.debug("Heartbeat OK")
                except TimeoutError:
                    logger.warning("Heartbeat pong timed out — connection may be stale")
                    self._state = ConnectionState.DISCONNECTED
                    break
                except ConnectionClosed:
                    logger.warning("Heartbeat detected closed connection")
                    self._state = ConnectionState.DISCONNECTED
                    break
                except Exception as e:
                    logger.warning("Heartbeat ping failed: %s", e)
                    self._state = ConnectionState.DISCONNECTED
                    break
        except asyncio.CancelledError:
            logger.debug("Heartbeat task cancelled")

    # --- Internal helpers ---

    async def _close_ws(self) -> None:
        """Safely close the WebSocket connection."""
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None


def _backoff_delay(attempt: int, base: float, maximum: float) -> float:
    """Calculate exponential backoff delay with full jitter.

    Formula: random(0, min(max_delay, base * 2^attempt))
    Reference: AWS Architecture Blog — Exponential Backoff and Jitter
    https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/

    Args:
        attempt: The current attempt number (1-indexed).
        base: Base delay in seconds.
        maximum: Maximum delay cap in seconds.

    Returns:
        Delay in seconds with jitter applied.
    """
    exp_delay = min(maximum, base * (2 ** (attempt - 1)))
    return random.uniform(0, exp_delay)  # noqa: S311
