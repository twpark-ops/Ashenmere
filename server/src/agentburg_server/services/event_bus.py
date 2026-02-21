"""NATS JetStream event bus — async publish/subscribe for simulation events.

Provides a module-level singleton that gracefully degrades when NATS
is unavailable so the server can run without the message broker.
"""

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.errors import (
    ConnectionClosedError,
    NoServersError,
    TimeoutError as NATSTimeoutError,
)
from nats.js.client import JetStreamContext

from agentburg_server.config import settings

logger = logging.getLogger(__name__)

# Stream configuration
STREAM_NAME = "AGENTBURG"
STREAM_SUBJECTS = [
    "agentburg.trade.*",
    "agentburg.verdict.*",
    "agentburg.tick.*",
    "agentburg.action.*",
]


class EventBus:
    """Async NATS JetStream event bus with graceful degradation.

    When NATS is unavailable, publish/subscribe calls are silently
    skipped with a warning log — the server continues to operate normally.
    """

    def __init__(self) -> None:
        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        """Return True if NATS connection is active."""
        return self._connected and self._nc is not None and not self._nc.is_closed

    async def connect(self) -> None:
        """Connect to NATS and set up the JetStream stream.

        Logs a warning and continues if NATS is unreachable.
        """
        try:
            self._nc = await nats.connect(settings.nats_url)
            self._js = self._nc.jetstream()

            # Create or update the stream
            await self._js.add_stream(
                name=STREAM_NAME,
                subjects=STREAM_SUBJECTS,
            )

            self._connected = True
            logger.info(
                "NATS JetStream connected (%s), stream '%s' ready",
                settings.nats_url,
                STREAM_NAME,
            )
        except (NoServersError, NATSTimeoutError, ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "NATS unavailable at %s — event bus disabled: %s",
                settings.nats_url,
                exc,
            )
            self._nc = None
            self._js = None
            self._connected = False

    async def disconnect(self) -> None:
        """Gracefully close the NATS connection."""
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            self._connected = False
            logger.info("NATS connection closed")

    async def publish(self, subject: str, data: dict[str, Any]) -> None:
        """Publish a JSON-serialized message to a JetStream subject.

        Silently skips if NATS is not connected.
        """
        if not self.connected or self._js is None:
            return

        try:
            payload = json.dumps(data, default=str).encode()
            await self._js.publish(subject, payload)
            logger.debug("Published to %s (%d bytes)", subject, len(payload))
        except (ConnectionClosedError, NATSTimeoutError) as exc:
            logger.warning("Failed to publish to %s: %s", subject, exc)
            self._connected = False

    async def subscribe(
        self,
        subject: str,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
        durable: str | None = None,
    ) -> None:
        """Subscribe to a JetStream subject with a push-based consumer.

        The callback receives the deserialized JSON dict for each message.
        Silently skips if NATS is not connected.
        """
        if not self.connected or self._js is None:
            logger.warning("Cannot subscribe to %s — NATS not connected", subject)
            return

        async def _msg_handler(msg: Any) -> None:
            try:
                data = json.loads(msg.data.decode())
                await callback(data)
                await msg.ack()
            except Exception:
                logger.exception("Error handling message on %s", subject)

        try:
            subscribe_kwargs: dict[str, Any] = {}
            if durable:
                subscribe_kwargs["durable"] = durable

            await self._js.subscribe(subject, cb=_msg_handler, **subscribe_kwargs)
            logger.info("Subscribed to %s", subject)
        except (ConnectionClosedError, NATSTimeoutError) as exc:
            logger.warning("Failed to subscribe to %s: %s", subject, exc)


# Module-level singleton (mirrors db.py pattern)
event_bus = EventBus()
