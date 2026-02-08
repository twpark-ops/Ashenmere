"""Tests for the ServerConnection — state machine, backoff, and message helpers."""

import pytest

from agentburg_client.config import AgentConfig, ServerConfig
from agentburg_client.connection import ConnectionState, ServerConnection, _backoff_delay

# ---------- _backoff_delay ----------


class TestBackoffDelay:
    """Test exponential backoff with full jitter."""

    def test_first_attempt_bounded(self):
        # attempt=1, base=1.0, max=60.0 → delay in [0, 1.0]
        for _ in range(50):
            delay = _backoff_delay(1, 1.0, 60.0)
            assert 0.0 <= delay <= 1.0

    def test_second_attempt_doubles(self):
        # attempt=2, base=1.0, max=60.0 → delay in [0, 2.0]
        for _ in range(50):
            delay = _backoff_delay(2, 1.0, 60.0)
            assert 0.0 <= delay <= 2.0

    def test_large_attempt_capped_at_max(self):
        # attempt=100, base=1.0, max=30.0 → delay in [0, 30.0]
        for _ in range(50):
            delay = _backoff_delay(100, 1.0, 30.0)
            assert 0.0 <= delay <= 30.0

    def test_jitter_produces_variety(self):
        # Multiple calls should produce different values (probabilistic)
        delays = {_backoff_delay(5, 1.0, 60.0) for _ in range(20)}
        assert len(delays) > 1, "Expected jitter to produce varied delays"

    def test_base_scales_correctly(self):
        # With base=5.0, attempt=1 → delay in [0, 5.0]
        for _ in range(50):
            delay = _backoff_delay(1, 5.0, 60.0)
            assert 0.0 <= delay <= 5.0


# ---------- ConnectionState lifecycle ----------


class TestConnectionState:
    """Test connection state values and transitions."""

    def test_all_states_are_strings(self):
        for state in ConnectionState:
            assert isinstance(state.value, str)

    def test_initial_state_is_disconnected(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        assert conn.state == ConnectionState.DISCONNECTED
        assert not conn.connected

    def test_request_shutdown_sets_state(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        conn.request_shutdown()
        assert conn.state == ConnectionState.SHUTTING_DOWN
        assert not conn.connected


# ---------- ServerConnection properties ----------


class TestServerConnectionProperties:
    """Test connection property accessors."""

    def test_connected_false_when_no_ws(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        assert not conn.connected

    def test_agent_id_initially_none(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        assert conn.agent_id is None


# ---------- send_action / send_query without connection ----------


class TestServerConnectionErrors:
    """Test error handling for operations without a connection."""

    @pytest.mark.asyncio
    async def test_send_action_raises_when_disconnected(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        with pytest.raises(ConnectionError, match="Not connected"):
            from agentburg_shared.protocol.messages import ActionType
            await conn.send_action(ActionType.IDLE)

    @pytest.mark.asyncio
    async def test_send_query_raises_when_disconnected(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        with pytest.raises(ConnectionError, match="Not connected"):
            from agentburg_shared.protocol.messages import QueryType
            await conn.send_query(QueryType.MY_BALANCE)

    @pytest.mark.asyncio
    async def test_receive_raises_when_disconnected(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        with pytest.raises(ConnectionError, match="Not connected"):
            await conn.receive()


# ---------- get_message timeout ----------


class TestGetMessage:
    """Test message queue with timeout."""

    @pytest.mark.asyncio
    async def test_get_message_returns_none_on_timeout(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        result = await conn.get_message(timeout=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_message_returns_queued_message(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        # Manually put a message in the queue
        await conn._message_queue.put({"type": "tick_update", "tick": 42})
        result = await conn.get_message(timeout=1.0)
        assert result is not None
        assert result["tick"] == 42


# ---------- disconnect ----------


class TestDisconnect:
    """Test graceful disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect_sets_disconnected(self):
        config = AgentConfig(server=ServerConfig(url="ws://localhost:8000/ws"))
        conn = ServerConnection(config)
        await conn.disconnect()
        assert conn.state == ConnectionState.DISCONNECTED
