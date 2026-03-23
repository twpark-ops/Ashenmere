"""Entry point for the agent brain client.

Usage: python -m agentburg_client [--config path/to/config.yaml] [--log-level INFO]

Features:
    - Structured logging (JSON or text format)
    - Graceful shutdown on SIGTERM/SIGINT
    - Crash recovery with restart
    - Auto-reconnection on connection loss
    - Memory persistence lifecycle management
"""

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
from typing import Any

from agentburg_shared.protocol.messages import MessageType

from agentburg_client.brain import AgentBrain
from agentburg_client.config import AgentConfig, load_config
from agentburg_client.connection import ConnectionState, ServerConnection
from agentburg_client.memory import MemoryCategory

logger = logging.getLogger("agentburg.agent")

# Maximum number of crash-restart cycles before giving up
MAX_CRASH_RESTARTS = 5
# Delay between crash-restart attempts (seconds)
CRASH_RESTART_DELAY = 5.0


class JsonFormatter(logging.Formatter):
    """JSON structured log formatter for machine-readable output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure the root logger with the specified format and level.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        fmt: Format type — 'text' for human-readable, 'json' for structured.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run_agent(config: AgentConfig) -> None:
    """Main agent loop: connect, receive ticks, make decisions, send actions.

    Handles reconnection on connection loss and memory persistence lifecycle.
    """
    brain = AgentBrain(config)
    conn = ServerConnection(config)

    logger.info(
        "Starting agent '%s' (%s)",
        config.personality.name,
        config.personality.title,
    )

    # Initialize memory persistence
    await brain.memory.initialize()
    loaded = await brain.memory.load()
    if loaded > 0:
        logger.info("Restored %d memories from previous session", loaded)

    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def _signal_handler(sig: signal.Signals) -> None:
        logger.info("Received %s, initiating graceful shutdown...", sig.name)
        conn.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig)

    try:
        # Connect to server
        if not await conn.connect():
            logger.error("Failed to connect to server. Exiting.")
            return

        logger.info("Connected! Agent ID: %s", conn.agent_id)

        # Start listener task
        listener = asyncio.create_task(conn.listen(), name="listener")

        try:
            await _agent_loop(config, brain, conn, listener)
        finally:
            if not listener.done():
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await listener

    finally:
        # Persist memories before exit
        persisted = await brain.memory.persist()
        if persisted > 0:
            logger.info("Persisted %d memories to database", persisted)
        await brain.memory.close()
        await conn.disconnect()

        # Log final token usage stats
        usage = brain.token_usage
        logger.info(
            "Session stats — decisions: %d, failures: %d, tokens (in: %d, out: %d)",
            usage.total_decisions,
            usage.total_failures,
            usage.total_input_tokens,
            usage.total_output_tokens,
        )
        logger.info("Agent disconnected. Goodbye from %s!", config.personality.name)


async def _agent_loop(
    config: AgentConfig,
    brain: AgentBrain,
    conn: ServerConnection,
    listener: asyncio.Task[None],
) -> None:
    """Inner message processing loop with reconnection support."""
    persist_interval = 50  # Persist memories every N ticks
    tick_counter = 0

    while conn.state != ConnectionState.SHUTTING_DOWN:
        # Check if we need to reconnect
        if not conn.connected and conn.state != ConnectionState.SHUTTING_DOWN:
            logger.warning("Connection lost, attempting reconnection...")
            if not listener.done():
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await listener

            if not await conn.reconnect():
                logger.error("Reconnection failed. Exiting agent loop.")
                return

            # Restart listener after reconnect
            listener = asyncio.create_task(conn.listen(), name="listener")

        msg = await conn.get_message(timeout=60.0)
        if msg is None:
            logger.debug("No message received, continuing...")
            continue

        msg_type = msg.get("type")
        tick = msg.get("tick", 0)

        if msg_type == MessageType.TICK_UPDATE:
            tick_counter += 1

            # Make a decision based on the tick update
            decision = await brain.decide(msg)

            action = decision["action"]
            params = decision["params"]
            reasoning = decision.get("reasoning", "")

            logger.info(
                "Tick %d | Action: %s | %s",
                tick,
                action,
                reasoning[:80],
            )

            # Send the action
            try:
                await conn.send_action(action, params)
            except ConnectionError:
                logger.warning("Failed to send action — connection lost")
                continue

            # Periodic memory persistence
            if tick_counter % persist_interval == 0:
                persisted = await brain.memory.persist()
                if persisted > 0:
                    logger.debug("Periodic persist: %d memories saved", persisted)
                # Prune if buffer is getting full
                if brain.memory.size() > config.memory_size * 0.9:
                    brain.memory.prune()

        elif msg_type == MessageType.ACTION_RESULT:
            success = msg.get("success", False)
            action = msg.get("action", "unknown")
            message = msg.get("message", "")
            level = logging.INFO if success else logging.WARNING
            logger.log(
                level,
                "Action result [%s]: %s — %s",
                action,
                "OK" if success else "FAIL",
                message,
            )
            brain.process_observation(
                f"Action {action}: {'succeeded' if success else 'failed'} — {message}",
                tick=tick,
            )

        elif msg_type == MessageType.QUERY_RESULT:
            query = msg.get("query", "unknown")
            data = msg.get("data", {})
            logger.info(
                "Query result [%s]: %d data keys",
                query,
                len(data),
            )
            # Store query results as knowledge for future decisions
            summary_parts = []
            for key, value in list(data.items())[:5]:
                if isinstance(value, list):
                    summary_parts.append(f"{key}: {len(value)} items")
                elif isinstance(value, (int, float)):
                    summary_parts.append(f"{key}={value}")
                else:
                    summary_parts.append(f"{key}: {str(value)[:50]}")
            if summary_parts:
                brain.memory.store(
                    f"Query {query}: {', '.join(summary_parts)}",
                    category=MemoryCategory.KNOWLEDGE,
                    tick=tick,
                )

        elif msg_type == MessageType.OBSERVATION:
            event = msg.get("event", "")
            logger.info("Observation: %s", event)
            brain.process_observation(event, tick=tick)

        elif msg_type == MessageType.WORLD_EVENT:
            event = msg.get("event", "")
            severity = msg.get("severity", "info")
            logger.info("World event [%s]: %s", severity, event)
            brain.process_observation(f"World: {event}", tick=tick)

        elif msg_type == MessageType.SLEEP_SUMMARY:
            ticks_missed = msg.get("ticks_missed", 0)
            balance_change = msg.get("balance_change", 0)
            events = msg.get("events", [])
            logger.info(
                "Sleep summary: missed %d ticks, balance change: %d",
                ticks_missed,
                balance_change,
            )
            for event in events[:5]:
                brain.memory.store(
                    f"While sleeping: {event}",
                    category=MemoryCategory.KNOWLEDGE,
                    tick=tick,
                )

        elif msg_type == MessageType.ERROR:
            logger.error(
                "Server error [%s]: %s",
                msg.get("code"),
                msg.get("message"),
            )

        else:
            logger.debug("Unknown message type: %s", msg_type)


def main() -> None:
    """CLI entry point with crash recovery."""
    parser = argparse.ArgumentParser(
        description="Ashenmere Agent Brain",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to agent config YAML",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level from config",
    )
    parser.add_argument(
        "--log-format",
        default=None,
        choices=["text", "json"],
        help="Override log format from config",
    )
    args = parser.parse_args()

    # Load config first to get logging settings
    try:
        config = load_config(args.config)
    except Exception as e:
        # Fallback logging for config parse errors
        logging.basicConfig(level=logging.ERROR)
        logger.error("Failed to load config from '%s': %s", args.config, e)
        sys.exit(1)

    # CLI args override config
    log_level = args.log_level or config.log_level
    log_format = args.log_format or config.log_format
    setup_logging(level=log_level, fmt=log_format)

    # Crash recovery loop
    crash_count = 0
    while crash_count < MAX_CRASH_RESTARTS:
        try:
            asyncio.run(run_agent(config))
            # Clean exit (shutdown signal or connect failure)
            break
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, exiting.")
            break
        except SystemExit:
            break
        except Exception:
            crash_count += 1
            logger.exception(
                "AGENT CRASHED (crash #%d/%d). Restarting in %.0fs...",
                crash_count,
                MAX_CRASH_RESTARTS,
                CRASH_RESTART_DELAY,
            )
            if crash_count >= MAX_CRASH_RESTARTS:
                logger.error(
                    "Max crash restarts (%d) exceeded. Giving up.",
                    MAX_CRASH_RESTARTS,
                )
                sys.exit(1)
            time.sleep(CRASH_RESTART_DELAY)

    sys.exit(0)


if __name__ == "__main__":
    main()
