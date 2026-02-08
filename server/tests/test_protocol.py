"""Tests for the WebSocket protocol message definitions."""

from uuid import uuid4

from agentburg_shared.protocol.messages import (
    ActionMessage,
    ActionResult,
    ActionType,
    AgentSnapshot,
    AuthenticateMessage,
    ErrorMessage,
    MarketSnapshot,
    QueryMessage,
    QueryType,
    TickUpdate,
)


def test_authenticate_message():
    msg = AuthenticateMessage(agent_token="test-token-123")
    data = msg.model_dump(mode="json")

    assert data["type"] == "authenticate"
    assert data["agent_token"] == "test-token-123"
    assert data["client_version"] == "0.1.0"


def test_action_message():
    msg = ActionMessage(
        action=ActionType.BUY,
        params={"item": "wheat", "price": 100, "quantity": 5},
    )
    data = msg.model_dump(mode="json")

    assert data["type"] == "action"
    assert data["action"] == "buy"
    assert data["params"]["item"] == "wheat"
    assert data["params"]["price"] == 100


def test_query_message():
    msg = QueryMessage(query=QueryType.MARKET_PRICES)
    data = msg.model_dump(mode="json")

    assert data["type"] == "query"
    assert data["query"] == "market_prices"


def test_tick_update():
    agent = AgentSnapshot(
        agent_id=uuid4(),
        name="TestAgent",
        balance=10000,
        inventory={"wheat": 10},
        reputation=500,
    )
    market = MarketSnapshot(
        prices={"wheat": 100, "bread": 200},
        trending_up=["wheat"],
    )
    tick = TickUpdate(
        tick=42,
        world_time="2026-01-01T12:00:00Z",
        agent=agent,
        market=market,
        observations=["A new trade was completed nearby"],
    )
    data = tick.model_dump(mode="json")

    assert data["type"] == "tick_update"
    assert data["tick"] == 42
    assert data["agent"]["balance"] == 10000
    assert data["market"]["prices"]["wheat"] == 100
    assert "wheat" in data["market"]["trending_up"]


def test_action_result():
    result = ActionResult(
        success=True,
        action=ActionType.BUY,
        message="Order placed",
        data={"order_id": "123"},
    )
    data = result.model_dump(mode="json")

    assert data["type"] == "action_result"
    assert data["success"] is True
    assert data["action"] == "buy"


def test_error_message():
    err = ErrorMessage(
        code="INSUFFICIENT_FUNDS",
        message="Not enough balance",
        details={"required": 1000, "available": 500},
    )
    data = err.model_dump(mode="json")

    assert data["type"] == "error"
    assert data["code"] == "INSUFFICIENT_FUNDS"
    assert data["details"]["required"] == 1000


def test_all_action_types():
    """Verify all 19 action types are defined."""
    assert len(ActionType) == 19
    assert ActionType.BUY == "buy"
    assert ActionType.SUE == "sue"
    assert ActionType.IDLE == "idle"


def test_all_query_types():
    """Verify all 10 query types are defined."""
    assert len(QueryType) == 10


def test_message_roundtrip():
    """Test serialization → deserialization roundtrip."""
    original = ActionMessage(
        action=ActionType.SELL,
        params={"item": "bread", "price": 250, "quantity": 3},
        request_id=uuid4(),
    )
    json_data = original.model_dump(mode="json")
    restored = ActionMessage.model_validate(json_data)

    assert restored.action == original.action
    assert restored.params == original.params
    assert restored.request_id == original.request_id
