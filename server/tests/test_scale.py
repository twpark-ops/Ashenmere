"""Scale tests — verify server performance with many concurrent agents.

These tests use the in-memory SQLite database and exercise:
- Bulk agent creation
- Concurrent action handling
- Tick processing with many orders/cases
- Plugin system under load
"""

from __future__ import annotations

import statistics
import time
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import agentburg_server.db as _db
from agentburg_server.models.agent import Agent, AgentStatus, AgentTier
from agentburg_server.models.economy import OrderSide
from agentburg_server.plugins.manager import PluginManager
from agentburg_server.services.market import place_order, run_batch_auction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agents(session: AsyncSession, count: int) -> list[Agent]:
    """Bulk-create agents for scale testing."""
    agents = []
    for i in range(count):
        agent = Agent(
            id=uuid4(),
            name=f"ScaleAgent-{i:04d}",
            api_token_hash=sha256(f"scale-token-{i}".encode()).hexdigest(),
            tier=AgentTier.PLAYER,
            status=AgentStatus.ACTIVE,
            balance=100_000,
            inventory={"wheat": 50, "wood": 30, "iron": 10, "fish": 20},
            location="market_square",
            reputation=500,
            credit_score=700,
        )
        session.add(agent)
        agents.append(agent)

    await session.flush()
    return agents


@pytest.fixture(autouse=True)
def _override_db(db_engine):
    """Override the global session factory for action/query handlers."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    original = _db.get_session_factory

    def _override():
        return factory

    _db.get_session_factory = _override
    yield
    _db.get_session_factory = original


# ---------------------------------------------------------------------------
# Bulk agent creation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bulk_agent_creation(db_session: AsyncSession):
    """Creating 500 agents should complete in under 5 seconds."""
    t0 = time.monotonic()
    agents = await _create_agents(db_session, 500)
    elapsed = time.monotonic() - t0

    assert len(agents) == 500
    assert elapsed < 5.0, f"Bulk creation took {elapsed:.2f}s (limit 5s)"


# ---------------------------------------------------------------------------
# Concurrent market orders
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_market_orders(db_session: AsyncSession):
    """100 agents each placing a buy+sell order should complete quickly."""
    agents = await _create_agents(db_session, 100)

    t0 = time.monotonic()

    # Each agent places a buy order and a sell order
    # Sell orders use items agents already have in inventory (wheat, wood, iron)
    sell_items = ["wheat", "wood", "iron"]
    for i, agent in enumerate(agents):
        buy_item = ["fish", "wheat", "wood", "iron"][i % 4]
        sell_item = sell_items[i % len(sell_items)]
        price = 50 + (i % 50)

        await place_order(
            db_session,
            agent_id=agent.id,
            item=buy_item,
            side=OrderSide.BUY,
            price=price,
            quantity=1,
            tick=1,
        )
        await place_order(
            db_session,
            agent_id=agent.id,
            item=sell_item,
            side=OrderSide.SELL,
            price=price + 10,
            quantity=1,
            tick=1,
        )

    await db_session.flush()
    order_time = time.monotonic() - t0

    # Run batch auction
    t1 = time.monotonic()
    await run_batch_auction(db_session, tick=1)
    await db_session.flush()
    auction_time = time.monotonic() - t1

    assert order_time < 5.0, f"200 orders took {order_time:.2f}s"
    assert auction_time < 3.0, f"Batch auction took {auction_time:.2f}s"


# ---------------------------------------------------------------------------
# Action handler throughput
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_action_handler_throughput(db_session: AsyncSession):
    """50 agents each performing 10 actions = 500 total actions."""
    from agentburg_shared.protocol.messages import ActionMessage, ActionType

    from agentburg_server.services.action_handler import handle_action

    agents = await _create_agents(db_session, 50)

    action_times: list[float] = []
    success_count = 0

    for agent in agents:
        for i in range(10):
            actions = [
                ActionMessage(
                    request_id=str(uuid4()),
                    action=ActionType.IDLE,
                    params={},
                ),
                ActionMessage(
                    request_id=str(uuid4()),
                    action=ActionType.BUY,
                    params={"item": "wheat", "price": 50, "quantity": 1},
                ),
                ActionMessage(
                    request_id=str(uuid4()),
                    action=ActionType.CHAT,
                    params={"message": f"test message {i}"},
                ),
            ]
            action_msg = actions[i % len(actions)]

            t0 = time.monotonic()
            result = await handle_action(agent.id, action_msg)
            elapsed = time.monotonic() - t0

            action_times.append(elapsed)
            if result.success:
                success_count += 1

    total_actions = len(action_times)
    mean_ms = statistics.mean(action_times) * 1000
    p95_ms = sorted(action_times)[int(total_actions * 0.95)] * 1000

    assert success_count > total_actions * 0.5, f"Only {success_count}/{total_actions} succeeded"
    assert mean_ms < 100, f"Mean action time {mean_ms:.1f}ms exceeds 100ms"
    assert p95_ms < 500, f"P95 action time {p95_ms:.1f}ms exceeds 500ms"


# ---------------------------------------------------------------------------
# Query handler throughput
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_handler_throughput(db_session: AsyncSession):
    """50 agents each making 10 queries = 500 total queries."""
    from agentburg_shared.protocol.messages import QueryMessage, QueryType

    from agentburg_server.services.query_handler import handle_query

    agents = await _create_agents(db_session, 50)

    query_times: list[float] = []
    success_count = 0

    for agent in agents:
        for i in range(10):
            queries = [
                QueryMessage(
                    request_id=str(uuid4()),
                    query=QueryType.MY_BALANCE,
                    params={},
                ),
                QueryMessage(
                    request_id=str(uuid4()),
                    query=QueryType.MARKET_PRICES,
                    params={},
                ),
                QueryMessage(
                    request_id=str(uuid4()),
                    query=QueryType.WORLD_STATUS,
                    params={},
                ),
            ]
            query_msg = queries[i % len(queries)]

            t0 = time.monotonic()
            result = await handle_query(agent.id, query_msg)
            elapsed = time.monotonic() - t0

            query_times.append(elapsed)
            # QueryResult has no success field — presence of data indicates success
            if result.data is not None:
                success_count += 1

    total_queries = len(query_times)
    mean_ms = statistics.mean(query_times) * 1000
    p95_ms = sorted(query_times)[int(total_queries * 0.95)] * 1000

    assert success_count > total_queries * 0.8, f"Only {success_count}/{total_queries} succeeded"
    assert mean_ms < 100, f"Mean query time {mean_ms:.1f}ms exceeds 100ms"
    assert p95_ms < 500, f"P95 query time {p95_ms:.1f}ms exceeds 500ms"


# ---------------------------------------------------------------------------
# Plugin system under load
# ---------------------------------------------------------------------------


class BenchmarkPlugin(Plugin):
    """Plugin that tracks hook invocation counts and timings."""

    metadata = PluginMetadata(name="benchmark", version="1.0.0", priority=1)

    def __init__(self) -> None:
        self.before_tick_count = 0
        self.after_tick_count = 0
        self.before_action_count = 0
        self.after_action_count = 0

    async def before_tick(self, *, tick: int) -> None:
        self.before_tick_count += 1

    async def after_tick(self, *, tick, trades, verdicts, payments, interest, elapsed) -> None:
        self.after_tick_count += 1

    async def before_action(self, *, agent_id, action, params):
        self.before_action_count += 1
        return None

    async def after_action(self, *, agent_id, action, success, data) -> None:
        self.after_action_count += 1


@pytest.mark.anyio
async def test_plugin_dispatch_throughput():
    """Dispatching 1000 hook calls should complete quickly."""
    mgr = PluginManager()
    plugins = [BenchmarkPlugin() for _ in range(5)]
    for i, p in enumerate(plugins):
        # Give each a unique name
        p.metadata = PluginMetadata(name=f"bench_{i}", priority=i * 10)
        mgr.register(p)

    t0 = time.monotonic()

    for tick in range(1000):

    elapsed = time.monotonic() - t0

    # Each plugin should have been called 1000 times
    for p in plugins:
        assert p.before_tick_count == 1000

    # 1000 dispatches to 5 plugins = 5000 hook calls, should be fast
    assert elapsed < 2.0, f"5000 hook calls took {elapsed:.2f}s (limit 2s)"


@pytest.mark.anyio
async def test_plugin_before_action_dispatch_throughput():
    """Dispatching 500 before_action hooks with param checking."""
    mgr = PluginManager()
    plugin = BenchmarkPlugin()
    mgr.register(plugin)

    t0 = time.monotonic()

    for _i in range(500):
        await mgr.dispatch_before_action(
            agent_id=uuid4(),
            action="BUY",
            params={"item": "wheat", "price": 100},
        )

    elapsed = time.monotonic() - t0

    assert plugin.before_action_count == 500
    assert elapsed < 1.0, f"500 before_action dispatches took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Mixed workload
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mixed_workload(db_session: AsyncSession):
    """Simulate a realistic mixed workload: orders + queries + actions."""
    from agentburg_shared.protocol.messages import (
        ActionMessage,
        ActionType,
        QueryMessage,
        QueryType,
    )

    from agentburg_server.services.action_handler import handle_action
    from agentburg_server.services.query_handler import handle_query

    agents = await _create_agents(db_session, 30)

    t0 = time.monotonic()
    total_ops = 0
    successes = 0

    for agent in agents:
        # Place some market orders
        for i in range(3):
            result = await handle_action(
                agent.id,
                ActionMessage(
                    request_id=str(uuid4()),
                    action=ActionType.BUY if i % 2 == 0 else ActionType.SELL,
                    params={"item": "wheat", "price": 50 + i * 10, "quantity": 1},
                ),
            )
            total_ops += 1
            if result.success:
                successes += 1

        # Query balance
        q_result = await handle_query(
            agent.id,
            QueryMessage(
                request_id=str(uuid4()),
                query=QueryType.MY_BALANCE,
                params={},
            ),
        )
        total_ops += 1
        if q_result.data is not None:
            successes += 1

        # Chat
        result = await handle_action(
            agent.id,
            ActionMessage(
                request_id=str(uuid4()),
                action=ActionType.CHAT,
                params={"message": "market update"},
            ),
        )
        total_ops += 1
        if result.success:
            successes += 1

    elapsed = time.monotonic() - t0

    # 30 agents * 5 ops = 150 total ops
    assert total_ops == 150
    assert successes > 100, f"Only {successes}/{total_ops} ops succeeded"
    assert elapsed < 10.0, f"Mixed workload took {elapsed:.2f}s (limit 10s)"
