"""Plugin manager — registry, lifecycle, and hook dispatch."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from agentburg_server.plugins.base import HookType, Plugin

logger = logging.getLogger(__name__)


class PluginManager:
    """Central registry for plugins and hook dispatch.

    Plugins are sorted by priority (lower value = earlier execution).
    Hook dispatch is fire-and-forget by default — individual plugin
    errors are logged but do not block other plugins or the caller.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hooks: dict[HookType, list[Plugin]] = defaultdict(list)

    # -- Registration --------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        """Register a plugin and index its hook methods."""
        name = plugin.metadata.name
        if name in self._plugins:
            msg = f"Plugin already registered: {name}"
            raise ValueError(msg)

        self._plugins[name] = plugin

        # Index hooks by checking which methods are overridden
        hook_method_map: dict[HookType, str] = {
            HookType.ON_STARTUP: "on_startup",
            HookType.ON_SHUTDOWN: "on_shutdown",
            HookType.BEFORE_TICK: "before_tick",
            HookType.AFTER_TICK: "after_tick",
            HookType.BEFORE_ACTION: "before_action",
            HookType.AFTER_ACTION: "after_action",
            HookType.ON_AGENT_CONNECT: "on_agent_connect",
            HookType.ON_AGENT_DISCONNECT: "on_agent_disconnect",
            HookType.ON_TRADE: "on_trade",
            HookType.ON_VERDICT: "on_verdict",
        }

        for hook_type, method_name in hook_method_map.items():
            # Only register if the plugin overrides the base method
            plugin_method = getattr(type(plugin), method_name, None)
            base_method = getattr(Plugin, method_name, None)
            if plugin_method is not None and plugin_method is not base_method:
                self._hooks[hook_type].append(plugin)
                # Re-sort by priority after insertion
                self._hooks[hook_type].sort(key=lambda p: p.metadata.priority)

        logger.info("Plugin registered: %s v%s (priority=%d)", name, plugin.metadata.version, plugin.metadata.priority)

    def unregister(self, name: str) -> None:
        """Unregister a plugin by name."""
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            return

        for hook_list in self._hooks.values():
            if plugin in hook_list:
                hook_list.remove(plugin)

        logger.info("Plugin unregistered: %s", name)

    # -- Query ---------------------------------------------------------------

    def get_plugin(self, name: str) -> Plugin | None:
        """Get a registered plugin by name."""
        return self._plugins.get(name)

    @property
    def plugins(self) -> list[Plugin]:
        """All registered plugins sorted by priority."""
        return sorted(self._plugins.values(), key=lambda p: p.metadata.priority)

    @property
    def plugin_names(self) -> list[str]:
        """Names of all registered plugins."""
        return list(self._plugins.keys())

    # -- Hook dispatch -------------------------------------------------------

    async def dispatch(self, hook_type: HookType, **kwargs: Any) -> None:
        """Dispatch a hook to all registered plugins.

        Errors in individual plugins are logged but do not propagate.
        """
        for plugin in self._hooks.get(hook_type, []):
            method = getattr(plugin, hook_type.value, None)
            if method is None:
                continue
            try:
                await method(**kwargs)
            except Exception:
                logger.exception("Plugin %s failed on hook %s", plugin.name, hook_type.value)

    async def dispatch_before_action(self, **kwargs: Any) -> dict[str, Any] | None:
        """Dispatch before_action hooks, allowing param override.

        If any plugin returns a dict, it replaces the action params.
        If any plugin raises ValueError, the action is blocked.
        """
        overridden_params: dict[str, Any] | None = None

        for plugin in self._hooks.get(HookType.BEFORE_ACTION, []):
            try:
                result = await plugin.before_action(**kwargs)
                if result is not None:
                    overridden_params = result
            except ValueError:
                raise  # Let ValueError propagate to block the action
            except Exception:
                logger.exception("Plugin %s failed on before_action", plugin.name)

        return overridden_params

    # -- Lifecycle -----------------------------------------------------------

    async def startup(self) -> None:
        """Call on_startup for all registered plugins."""
        await self.dispatch(HookType.ON_STARTUP)

    async def shutdown(self) -> None:
        """Call on_shutdown for all registered plugins."""
        await self.dispatch(HookType.ON_SHUTDOWN)

    def clear(self) -> None:
        """Remove all plugins. Useful for testing."""
        self._plugins.clear()
        self._hooks.clear()


# Global singleton — used throughout the server
plugin_manager = PluginManager()
