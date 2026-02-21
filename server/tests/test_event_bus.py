"""Tests for the NATS JetStream event bus service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentburg_server.services.event_bus import EventBus


@pytest.fixture
def bus() -> EventBus:
    """Return a fresh EventBus instance (not the singleton)."""
    return EventBus()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_success(bus: EventBus):
    """Successful connection should set connected=True and create the stream."""
    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    # jetstream() is a sync method in nats-py, so use MagicMock for the client
    mock_nc.jetstream.return_value = mock_js

    with patch("agentburg_server.services.event_bus.nats") as mock_nats:
        mock_nats.connect = AsyncMock(return_value=mock_nc)
        await bus.connect()

    assert bus.connected is True
    mock_js.add_stream.assert_awaited_once()
    call_kwargs = mock_js.add_stream.call_args
    assert call_kwargs[1]["name"] == "AGENTBURG"
    assert "agentburg.trade.*" in call_kwargs[1]["subjects"]


@pytest.mark.anyio
async def test_connect_nats_unavailable(bus: EventBus):
    """When NATS is unreachable, connected should stay False without raising."""
    from nats.errors import NoServersError

    with patch("agentburg_server.services.event_bus.nats") as mock_nats:
        mock_nats.connect = AsyncMock(side_effect=NoServersError)
        await bus.connect()

    assert bus.connected is False


@pytest.mark.anyio
async def test_connect_connection_refused(bus: EventBus):
    """ConnectionRefusedError should be handled gracefully."""
    with patch("agentburg_server.services.event_bus.nats") as mock_nats:
        mock_nats.connect = AsyncMock(side_effect=ConnectionRefusedError)
        await bus.connect()

    assert bus.connected is False


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disconnect(bus: EventBus):
    """Disconnect should drain the NATS connection."""
    mock_nc = AsyncMock()
    mock_nc.is_closed = False
    bus._nc = mock_nc
    bus._connected = True

    await bus.disconnect()

    mock_nc.drain.assert_awaited_once()
    assert bus._connected is False


@pytest.mark.anyio
async def test_disconnect_already_closed(bus: EventBus):
    """Disconnect on already-closed connection should be a no-op."""
    mock_nc = AsyncMock()
    mock_nc.is_closed = True
    bus._nc = mock_nc

    await bus.disconnect()

    mock_nc.drain.assert_not_awaited()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_success(bus: EventBus):
    """Publish should serialize to JSON and call JetStream publish."""
    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    data = {"tick": 42, "trades": 3}
    await bus.publish("agentburg.tick.42", data)

    mock_js.publish.assert_awaited_once()
    call_args = mock_js.publish.call_args
    assert call_args[0][0] == "agentburg.tick.42"
    assert json.loads(call_args[0][1].decode()) == data


@pytest.mark.anyio
async def test_publish_not_connected(bus: EventBus):
    """Publish when not connected should silently skip."""
    assert bus.connected is False
    # Should not raise
    await bus.publish("agentburg.tick.1", {"tick": 1})


@pytest.mark.anyio
async def test_publish_connection_lost(bus: EventBus):
    """If NATS drops during publish, connected should become False."""
    from nats.errors import ConnectionClosedError

    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    mock_js.publish.side_effect = ConnectionClosedError
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    await bus.publish("agentburg.tick.1", {"tick": 1})

    assert bus._connected is False


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subscribe_success(bus: EventBus):
    """Subscribe should register a push consumer on the JetStream subject."""
    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    callback = AsyncMock()
    await bus.subscribe("agentburg.trade.*", callback)

    mock_js.subscribe.assert_awaited_once()
    call_args = mock_js.subscribe.call_args
    assert call_args[0][0] == "agentburg.trade.*"


@pytest.mark.anyio
async def test_subscribe_with_durable(bus: EventBus):
    """Subscribe with a durable name should pass it through."""
    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    callback = AsyncMock()
    await bus.subscribe("agentburg.trade.*", callback, durable="trade-consumer")

    call_kwargs = mock_js.subscribe.call_args[1]
    assert call_kwargs["durable"] == "trade-consumer"


@pytest.mark.anyio
async def test_subscribe_not_connected(bus: EventBus):
    """Subscribe when not connected should silently skip."""
    callback = AsyncMock()
    await bus.subscribe("agentburg.trade.*", callback)
    # Should not raise, callback should not be called


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_serializes_uuids(bus: EventBus):
    """UUID and other non-JSON-native types should be serialized via default=str."""
    from uuid import uuid4

    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    test_uuid = uuid4()
    data = {"agent_id": test_uuid, "tick": 1}
    await bus.publish("agentburg.action.test", data)

    call_args = mock_js.publish.call_args
    payload = json.loads(call_args[0][1].decode())
    assert payload["agent_id"] == str(test_uuid)
    assert payload["tick"] == 1


@pytest.mark.anyio
async def test_message_handler_deserializes_and_acks():
    """The internal message handler should deserialize JSON, call callback, and ack."""
    bus = EventBus()
    mock_nc = MagicMock()
    mock_nc.is_closed = False
    mock_js = AsyncMock()
    bus._nc = mock_nc
    bus._js = mock_js
    bus._connected = True

    received: list[dict] = []

    async def on_msg(data: dict) -> None:
        received.append(data)

    await bus.subscribe("agentburg.tick.*", on_msg)

    # Extract the internal _msg_handler that was passed to js.subscribe
    subscribe_call = mock_js.subscribe.call_args
    internal_handler = subscribe_call[1]["cb"]

    # Simulate a NATS message
    mock_msg = AsyncMock()
    mock_msg.data = json.dumps({"tick": 99}).encode()

    await internal_handler(mock_msg)

    assert len(received) == 1
    assert received[0] == {"tick": 99}
    mock_msg.ack.assert_awaited_once()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_module_singleton():
    """The module-level event_bus should be an EventBus instance."""
    from agentburg_server.services.event_bus import event_bus

    assert isinstance(event_bus, EventBus)
    assert event_bus.connected is False  # Not connected in test environment
