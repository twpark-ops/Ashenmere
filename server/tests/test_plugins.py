"""Tests for the plugin system — registration, dispatch, lifecycle, built-in plugins."""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentburg_server.plugins.base import HookType, Plugin, PluginMetadata
from agentburg_server.plugins.manager import PluginManager

# ---------------------------------------------------------------------------
# Test plugins
# ---------------------------------------------------------------------------


class TickCounterPlugin(Plugin):
    """Test plugin that counts ticks."""

    metadata = PluginMetadata(
        name="tick_counter",
        version="0.1.0",
        description="Counts ticks for testing",
        priority=10,
    )

    def __init__(self) -> None:
        self.before_count = 0
        self.after_count = 0
        self.last_tick = -1
        self.last_elapsed = 0.0

    async def before_tick(self, *, tick: int) -> None:
        self.before_count += 1
        self.last_tick = tick

    async def after_tick(
        self, *, tick: int, trades: int, verdicts: int, payments: int, interest: int, elapsed: float
    ) -> None:
        self.after_count += 1
        self.last_elapsed = elapsed


class ActionLoggerPlugin(Plugin):
    """Test plugin that logs actions."""

    metadata = PluginMetadata(
        name="action_logger",
        version="0.1.0",
        priority=20,
    )

    def __init__(self) -> None:
        self.actions: list[dict] = []
        self.results: list[dict] = []

    async def before_action(self, *, agent_id, action, params):
        self.actions.append({"agent_id": agent_id, "action": action, "params": params})
        return None

    async def after_action(self, *, agent_id, action, success, data):
        self.results.append({"agent_id": agent_id, "action": action, "success": success})


class ParamOverridePlugin(Plugin):
    """Test plugin that overrides action params."""

    metadata = PluginMetadata(name="param_override", priority=30)

    async def before_action(self, *, agent_id, action, params):
        # Double the price if present
        if "price" in params:
            return {**params, "price": params["price"] * 2}
        return None


class ActionBlockerPlugin(Plugin):
    """Test plugin that blocks certain actions."""

    metadata = PluginMetadata(name="action_blocker", priority=5)

    def __init__(self, blocked_action: str = "CHAT") -> None:
        self._blocked = blocked_action

    async def before_action(self, *, agent_id, action, params):
        if action == self._blocked:
            msg = f"Action {action} is blocked by plugin"
            raise ValueError(msg)
        return None


class LifecyclePlugin(Plugin):
    """Test plugin that tracks startup/shutdown."""

    metadata = PluginMetadata(name="lifecycle_test", priority=50)

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def on_startup(self) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.stopped = True


class ConnectionPlugin(Plugin):
    """Test plugin that tracks agent connections."""

    metadata = PluginMetadata(name="connection_tracker", priority=40)

    def __init__(self) -> None:
        self.connected: list = []
        self.disconnected: list = []

    async def on_agent_connect(self, *, agent_id) -> None:
        self.connected.append(agent_id)

    async def on_agent_disconnect(self, *, agent_id) -> None:
        self.disconnected.append(agent_id)


class ErrorPlugin(Plugin):
    """Test plugin that raises errors in hooks."""

    metadata = PluginMetadata(name="error_plugin", priority=100)

    async def before_tick(self, *, tick: int) -> None:
        msg = "Plugin error!"
        raise RuntimeError(msg)

    async def after_action(self, *, agent_id, action, success, data) -> None:
        msg = "Plugin error!"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_plugin():
    """Registering a plugin should make it accessible by name."""
    mgr = PluginManager()
    plugin = TickCounterPlugin()
    mgr.register(plugin)

    assert "tick_counter" in mgr.plugin_names
    assert mgr.get_plugin("tick_counter") is plugin
    assert len(mgr.plugins) == 1


def test_register_duplicate_raises():
    """Registering a plugin with the same name twice must raise ValueError."""
    mgr = PluginManager()
    mgr.register(TickCounterPlugin())

    with pytest.raises(ValueError, match="already registered"):
        mgr.register(TickCounterPlugin())


def test_unregister_plugin():
    """Unregistering a plugin should remove it from all hook lists."""
    mgr = PluginManager()
    plugin = TickCounterPlugin()
    mgr.register(plugin)
    mgr.unregister("tick_counter")

    assert "tick_counter" not in mgr.plugin_names
    assert mgr.get_plugin("tick_counter") is None
    assert len(mgr.plugins) == 0


def test_unregister_nonexistent():
    """Unregistering a nonexistent plugin should be a no-op."""
    mgr = PluginManager()
    mgr.unregister("does_not_exist")  # Should not raise


def test_priority_ordering():
    """Plugins should be sorted by priority (lower = first)."""
    mgr = PluginManager()
    low = TickCounterPlugin()  # priority=10
    mid = ActionLoggerPlugin()  # priority=20
    high = ParamOverridePlugin()  # priority=30

    # Register out of order
    mgr.register(high)
    mgr.register(low)
    mgr.register(mid)

    names = [p.name for p in mgr.plugins]
    assert names == ["tick_counter", "action_logger", "param_override"]


def test_clear():
    """Clear should remove all plugins."""
    mgr = PluginManager()
    mgr.register(TickCounterPlugin())
    mgr.register(ActionLoggerPlugin())
    mgr.clear()

    assert len(mgr.plugins) == 0
    assert len(mgr.plugin_names) == 0


# ---------------------------------------------------------------------------
# Hook dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_before_tick():
    """Dispatching BEFORE_TICK should call the plugin's before_tick method."""
    mgr = PluginManager()
    plugin = TickCounterPlugin()
    mgr.register(plugin)

    await mgr.dispatch(HookType.BEFORE_TICK, tick=42)

    assert plugin.before_count == 1
    assert plugin.last_tick == 42


@pytest.mark.anyio
async def test_dispatch_after_tick():
    """Dispatching AFTER_TICK should call the plugin with all kwargs."""
    mgr = PluginManager()
    plugin = TickCounterPlugin()
    mgr.register(plugin)

    await mgr.dispatch(
        HookType.AFTER_TICK,
        tick=5,
        trades=3,
        verdicts=1,
        payments=2,
        interest=100,
        elapsed=0.05,
    )

    assert plugin.after_count == 1
    assert plugin.last_elapsed == 0.05


@pytest.mark.anyio
async def test_dispatch_multiple_plugins():
    """Multiple plugins should all receive the hook dispatch."""
    mgr = PluginManager()
    p1 = TickCounterPlugin()
    p2 = ActionLoggerPlugin()  # has no before_tick, should not crash
    mgr.register(p1)
    mgr.register(p2)

    await mgr.dispatch(HookType.BEFORE_TICK, tick=1)

    assert p1.before_count == 1


@pytest.mark.anyio
async def test_dispatch_error_isolation():
    """An error in one plugin should not prevent other plugins from running."""
    mgr = PluginManager()
    error_plugin = ErrorPlugin()
    counter = TickCounterPlugin()

    # Register error plugin first (priority 100)
    # but counter has priority 10, so counter runs first
    mgr.register(counter)
    mgr.register(error_plugin)

    await mgr.dispatch(HookType.BEFORE_TICK, tick=1)

    # Counter should still have been called even though error_plugin fails
    assert counter.before_count == 1


@pytest.mark.anyio
async def test_dispatch_no_listeners():
    """Dispatching a hook with no listeners should be a no-op."""
    mgr = PluginManager()
    await mgr.dispatch(HookType.BEFORE_TICK, tick=1)  # Should not raise


# ---------------------------------------------------------------------------
# Before action dispatch tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_before_action_passthrough():
    """Before_action returning None should not modify params."""
    mgr = PluginManager()
    plugin = ActionLoggerPlugin()
    mgr.register(plugin)

    result = await mgr.dispatch_before_action(agent_id=uuid4(), action="BUY", params={"item": "wood", "price": 100})

    assert result is None
    assert len(plugin.actions) == 1


@pytest.mark.anyio
async def test_before_action_param_override():
    """Before_action returning a dict should override params."""
    mgr = PluginManager()
    plugin = ParamOverridePlugin()
    mgr.register(plugin)

    result = await mgr.dispatch_before_action(agent_id=uuid4(), action="BUY", params={"item": "wood", "price": 100})

    assert result is not None
    assert result["price"] == 200  # Doubled by plugin


@pytest.mark.anyio
async def test_before_action_block():
    """Before_action raising ValueError should propagate to block the action."""
    mgr = PluginManager()
    mgr.register(ActionBlockerPlugin(blocked_action="CHAT"))

    with pytest.raises(ValueError, match="blocked by plugin"):
        await mgr.dispatch_before_action(agent_id=uuid4(), action="CHAT", params={"message": "hello"})


@pytest.mark.anyio
async def test_before_action_block_priority():
    """Blocker with lower priority should run before logger."""
    mgr = PluginManager()
    blocker = ActionBlockerPlugin(blocked_action="CHAT")  # priority=5
    logger_p = ActionLoggerPlugin()  # priority=20
    mgr.register(blocker)
    mgr.register(logger_p)

    with pytest.raises(ValueError, match="blocked by plugin"):
        await mgr.dispatch_before_action(agent_id=uuid4(), action="CHAT", params={"message": "hello"})

    # Logger should not have been called because blocker raised first
    assert len(logger_p.actions) == 0


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_shutdown():
    """startup() and shutdown() should call on_startup/on_shutdown hooks."""
    mgr = PluginManager()
    plugin = LifecyclePlugin()
    mgr.register(plugin)

    assert not plugin.started
    await mgr.startup()
    assert plugin.started

    assert not plugin.stopped
    await mgr.shutdown()
    assert plugin.stopped


@pytest.mark.anyio
async def test_connection_hooks():
    """on_agent_connect and on_agent_disconnect should be dispatched."""
    mgr = PluginManager()
    plugin = ConnectionPlugin()
    mgr.register(plugin)

    agent_id = uuid4()
    await mgr.dispatch(HookType.ON_AGENT_CONNECT, agent_id=agent_id)
    await mgr.dispatch(HookType.ON_AGENT_DISCONNECT, agent_id=agent_id)

    assert len(plugin.connected) == 1
    assert plugin.connected[0] == agent_id
    assert len(plugin.disconnected) == 1
    assert plugin.disconnected[0] == agent_id


# ---------------------------------------------------------------------------
# Built-in economy_stats plugin tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_economy_stats_after_tick():
    """EconomyStatsPlugin should track per-tick statistics."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin

    plugin = EconomyStatsPlugin(window_size=100)

    await plugin.after_tick(tick=1, trades=5, verdicts=2, payments=3, interest=100, elapsed=0.01)
    await plugin.after_tick(tick=2, trades=3, verdicts=0, payments=1, interest=0, elapsed=0.02)

    stats1 = plugin.get_tick_stats(1)
    assert stats1 is not None
    assert stats1.trades == 5
    assert stats1.verdicts == 2

    stats2 = plugin.get_tick_stats(2)
    assert stats2 is not None
    assert stats2.trades == 3

    summary = plugin.summary
    assert summary["total_trades"] == 8
    assert summary["total_verdicts"] == 2
    assert summary["ticks_tracked"] == 2


@pytest.mark.anyio
async def test_economy_stats_on_trade():
    """EconomyStatsPlugin should accumulate trade volume."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin

    plugin = EconomyStatsPlugin()

    await plugin.on_trade(
        session=None,  # type: ignore[arg-type]
        tick=1,
        buyer_id=uuid4(),
        seller_id=uuid4(),
        item="wood",
        price=100,
        quantity=5,
    )
    await plugin.on_trade(
        session=None,  # type: ignore[arg-type]
        tick=1,
        buyer_id=uuid4(),
        seller_id=uuid4(),
        item="iron",
        price=200,
        quantity=3,
    )

    stats = plugin.get_tick_stats(1)
    assert stats is not None
    assert stats.trade_volume == 100 * 5 + 200 * 3  # 1100

    assert plugin.summary["total_volume"] == 1100


@pytest.mark.anyio
async def test_economy_stats_on_verdict():
    """EconomyStatsPlugin should track guilty verdicts and fines."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin

    plugin = EconomyStatsPlugin()

    await plugin.on_verdict(
        session=None,  # type: ignore[arg-type]
        tick=1,
        case_id=uuid4(),
        plaintiff_id=uuid4(),
        defendant_id=uuid4(),
        guilty=True,
        fine=5000,
    )
    await plugin.on_verdict(
        session=None,  # type: ignore[arg-type]
        tick=1,
        case_id=uuid4(),
        plaintiff_id=uuid4(),
        defendant_id=uuid4(),
        guilty=False,
        fine=0,
    )

    stats = plugin.get_tick_stats(1)
    assert stats is not None
    assert stats.guilty_verdicts == 1
    assert stats.fines_collected == 5000

    assert plugin.summary["total_fines"] == 5000


@pytest.mark.anyio
async def test_economy_stats_window_pruning():
    """Old ticks should be pruned when exceeding window_size."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin

    plugin = EconomyStatsPlugin(window_size=5)

    for i in range(10):
        await plugin.after_tick(tick=i, trades=1, verdicts=0, payments=0, interest=0, elapsed=0.01)

    assert plugin.summary["ticks_tracked"] == 5
    # Only ticks 5-9 should remain
    assert plugin.get_tick_stats(0) is None
    assert plugin.get_tick_stats(9) is not None


@pytest.mark.anyio
async def test_economy_stats_recent_stats():
    """recent_stats should return the most recent N tick stats."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin

    plugin = EconomyStatsPlugin()

    for i in range(20):
        await plugin.after_tick(tick=i, trades=i, verdicts=0, payments=0, interest=0, elapsed=0.01)

    recent = plugin.recent_stats(5)
    assert len(recent) == 5
    # Should be in reverse order (most recent first)
    ticks = [t for t, _ in recent]
    assert ticks == [19, 18, 17, 16, 15]


# ---------------------------------------------------------------------------
# Plugin metadata tests
# ---------------------------------------------------------------------------


def test_plugin_repr():
    """Plugin repr should show name and version."""
    plugin = TickCounterPlugin()
    assert "tick_counter" in repr(plugin)
    assert "0.1.0" in repr(plugin)


def test_plugin_name_property():
    """Plugin.name should return metadata.name."""
    plugin = TickCounterPlugin()
    assert plugin.name == "tick_counter"


def test_plugin_metadata_frozen():
    """PluginMetadata should be immutable."""
    meta = PluginMetadata(name="test", version="1.0.0")
    with pytest.raises(AttributeError):
        meta.name = "changed"  # type: ignore[misc]


def test_hook_type_values():
    """HookType should have all expected values."""
    expected = {
        "on_startup",
        "on_shutdown",
        "before_tick",
        "after_tick",
        "before_action",
        "after_action",
        "on_agent_connect",
        "on_agent_disconnect",
        "on_trade",
        "on_verdict",
    }
    actual = {h.value for h in HookType}
    assert actual == expected
