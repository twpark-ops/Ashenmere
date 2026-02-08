"""Entry point for the agent brain client.

Usage: python -m agentburg_client [--config path/to/config.yaml]
"""

import argparse
import asyncio
import logging
import signal
import sys

from agentburg_client.config import load_config
from agentburg_client.brain import AgentBrain
from agentburg_client.connection import ServerConnection
from agentburg_shared.protocol.messages import ActionType, MessageType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger("agentburg.agent")


async def run_agent(config_path: str) -> None:
    """Main agent loop: connect, receive ticks, make decisions, send actions."""
    config = load_config(config_path)
    brain = AgentBrain(config)
    conn = ServerConnection(config)

    logger.info("Starting agent '%s' (%s)", config.personality.name, config.personality.title)

    # Connect to server
    if not await conn.connect():
        logger.error("Failed to connect to server. Exiting.")
        return

    logger.info("Connected! Agent ID: %s", conn.agent_id)

    # Start listener task
    listener = asyncio.create_task(conn.listen())

    try:
        while conn.connected:
            msg = await conn.get_message(timeout=60.0)
            if msg is None:
                logger.debug("No message received, continuing...")
                continue

            msg_type = msg.get("type")

            if msg_type == MessageType.TICK_UPDATE:
                # Make a decision based on the tick update
                decision = await brain.decide(msg)

                action = decision["action"]
                params = decision["params"]
                reasoning = decision.get("reasoning", "")

                logger.info(
                    "Tick %d | Action: %s | %s",
                    msg.get("tick", 0),
                    action,
                    reasoning[:80],
                )

                # Send the action
                await conn.send_action(action, params)

            elif msg_type == MessageType.ACTION_RESULT:
                success = msg.get("success", False)
                action = msg.get("action", "unknown")
                message = msg.get("message", "")
                level = logging.INFO if success else logging.WARNING
                logger.log(level, "Action result [%s]: %s — %s", action, "OK" if success else "FAIL", message)

                # Store result as memory
                brain.process_observation(f"Action {action}: {'succeeded' if success else 'failed'} — {message}")

            elif msg_type == MessageType.OBSERVATION:
                event = msg.get("event", "")
                logger.info("Observation: %s", event)
                brain.process_observation(event)

            elif msg_type == MessageType.WORLD_EVENT:
                event = msg.get("event", "")
                severity = msg.get("severity", "info")
                logger.info("World event [%s]: %s", severity, event)
                brain.process_observation(f"World: {event}")

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
                    brain.process_observation(f"While sleeping: {event}")

            elif msg_type == MessageType.ERROR:
                logger.error("Server error [%s]: %s", msg.get("code"), msg.get("message"))

            else:
                logger.debug("Unknown message type: %s", msg_type)

    except asyncio.CancelledError:
        logger.info("Agent shutting down...")
    finally:
        listener.cancel()
        await conn.disconnect()
        logger.info("Agent disconnected. Goodbye from %s!", config.personality.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentBurg Agent Brain")
    parser.add_argument("--config", default="config.yaml", help="Path to agent config YAML")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()

    def shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down...", sig.name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(run_agent(args.config))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
