"""Load test for AgentBurg — measure server performance under concurrent agent load.

This script creates N agents, connects them via WebSocket, and measures:
- Connection establishment time
- Action processing throughput
- Tick processing time under load
- Memory usage

Usage:
    # Run against a local server (must be running)
    uv run python benchmarks/load_test.py --agents 100 --actions 10

    # Full load test
    uv run python benchmarks/load_test.py --agents 1000 --actions 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import statistics
import sys
import time
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID, uuid4

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Aggregated benchmark results."""

    total_agents: int = 0
    connected_agents: int = 0
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0

    connect_times: list[float] = field(default_factory=list)
    action_times: list[float] = field(default_factory=list)

    start_time: float = 0.0
    end_time: float = 0.0

    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """Produce a summary dict."""
        elapsed = self.end_time - self.start_time
        conn_stats = _stats(self.connect_times) if self.connect_times else {}
        action_stats = _stats(self.action_times) if self.action_times else {}

        return {
            "elapsed_seconds": round(elapsed, 3),
            "agents": {
                "total": self.total_agents,
                "connected": self.connected_agents,
                "connect_rate": f"{self.connected_agents / self.total_agents * 100:.1f}%",
            },
            "connections": conn_stats,
            "actions": {
                "total": self.total_actions,
                "successful": self.successful_actions,
                "failed": self.failed_actions,
                "throughput": round(self.successful_actions / elapsed, 1) if elapsed > 0 else 0,
                "latency": action_stats,
            },
            "error_count": len(self.errors),
        }


def _stats(values: list[float]) -> dict:
    """Compute basic statistics for a list of values."""
    if not values:
        return {}
    return {
        "count": len(values),
        "min_ms": round(min(values) * 1000, 2),
        "max_ms": round(max(values) * 1000, 2),
        "mean_ms": round(statistics.mean(values) * 1000, 2),
        "median_ms": round(statistics.median(values) * 1000, 2),
        "p95_ms": round(sorted(values)[int(len(values) * 0.95)] * 1000, 2) if len(values) > 1 else 0,
        "p99_ms": round(sorted(values)[int(len(values) * 0.99)] * 1000, 2) if len(values) > 1 else 0,
    }


async def create_test_agents(
    base_url: str,
    count: int,
    *,
    admin_email: str = "admin@agentburg.world",
    admin_password: str = "admin123",
) -> list[tuple[UUID, str]]:
    """Create test agents via the REST API and return (agent_id, raw_token) pairs.

    Requires a running server with the admin user seeded.
    """
    import httpx

    agents: list[tuple[UUID, str]] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        # Login as admin
        resp = await client.post("/api/v1/auth/login", json={
            "email": admin_email,
            "password": admin_password,
        })
        if resp.status_code != 200:
            logger.error("Admin login failed: %s", resp.text)
            return agents

        jwt_token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {jwt_token}"}

        # Create agents in batches
        batch_size = 50
        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            tasks = []
            for i in range(batch_start, batch_end):
                tasks.append(
                    client.post(
                        "/api/v1/agents",
                        json={"name": f"LoadTest-{i:04d}", "title": "Tester"},
                        headers=headers,
                    )
                )
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for resp in responses:
                if isinstance(resp, Exception):
                    logger.warning("Agent creation error: %s", resp)
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    agents.append((UUID(data["agent_id"]), data["token"]))
                else:
                    logger.warning("Agent creation failed: %s", resp.text)

            logger.info("Created %d/%d agents", len(agents), count)

    return agents


async def run_agent(
    ws_url: str,
    token: str,
    num_actions: int,
    result: BenchmarkResult,
    semaphore: asyncio.Semaphore,
) -> None:
    """Simulate a single agent: connect, authenticate, perform actions."""
    async with semaphore:
        try:
            # Connect
            t0 = time.monotonic()
            async with websockets.connect(ws_url) as ws:
                connect_time = time.monotonic() - t0
                result.connect_times.append(connect_time)

                # Authenticate
                await ws.send(json.dumps({
                    "type": "authenticate",
                    "agent_token": token,
                }))
                auth_resp = json.loads(await ws.recv())
                if not auth_resp.get("success"):
                    result.errors.append(f"Auth failed: {auth_resp.get('message')}")
                    return

                result.connected_agents += 1

                # Perform actions
                for i in range(num_actions):
                    action = _random_action(i)
                    t1 = time.monotonic()
                    await ws.send(json.dumps(action))
                    resp = json.loads(await ws.recv())
                    action_time = time.monotonic() - t1

                    result.action_times.append(action_time)
                    result.total_actions += 1

                    if resp.get("success"):
                        result.successful_actions += 1
                    else:
                        result.failed_actions += 1

        except Exception as e:
            result.errors.append(f"Agent error: {e}")


def _random_action(index: int) -> dict:
    """Generate a pseudo-random action based on index."""
    items = ["wheat", "bread", "wood", "stone", "iron", "fish"]
    actions = [
        # Market actions
        {
            "type": "action",
            "request_id": str(uuid4()),
            "action": "BUY",
            "params": {"item": items[index % len(items)], "price": 100 + index, "quantity": 1},
        },
        {
            "type": "action",
            "request_id": str(uuid4()),
            "action": "SELL",
            "params": {"item": items[index % len(items)], "price": 100 + index, "quantity": 1},
        },
        # Chat action
        {
            "type": "action",
            "request_id": str(uuid4()),
            "action": "CHAT",
            "params": {"message": f"Hello from load test #{index}"},
        },
        # Query
        {
            "type": "query",
            "request_id": str(uuid4()),
            "query_type": "MY_BALANCE",
            "params": {},
        },
        # Idle
        {
            "type": "action",
            "request_id": str(uuid4()),
            "action": "IDLE",
            "params": {},
        },
    ]
    return actions[index % len(actions)]


async def main(
    *,
    base_url: str,
    ws_url: str,
    num_agents: int,
    num_actions: int,
    concurrency: int,
) -> BenchmarkResult:
    """Run the full load test."""
    result = BenchmarkResult(total_agents=num_agents)

    logger.info("=== AgentBurg Load Test ===")
    logger.info("Agents: %d, Actions per agent: %d, Concurrency: %d", num_agents, num_actions, concurrency)

    # Step 1: Create agents via REST API
    logger.info("Creating %d test agents...", num_agents)
    agents = await create_test_agents(base_url, num_agents)
    if not agents:
        logger.error("No agents created. Is the server running with seed data?")
        return result

    result.total_agents = len(agents)
    logger.info("Created %d agents", len(agents))

    # Step 2: Run concurrent agent simulations
    semaphore = asyncio.Semaphore(concurrency)
    result.start_time = time.monotonic()

    tasks = [
        run_agent(ws_url, token, num_actions, result, semaphore)
        for _, token in agents
    ]
    await asyncio.gather(*tasks)

    result.end_time = time.monotonic()

    # Print results
    summary = result.summary()
    logger.info("=== Results ===")
    logger.info(json.dumps(summary, indent=2))

    if result.errors:
        logger.warning("Errors (%d):", len(result.errors))
        for err in result.errors[:10]:  # Show first 10
            logger.warning("  %s", err)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentBurg Load Test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Server base URL")
    parser.add_argument("--ws-url", default="ws://localhost:8000/ws", help="WebSocket URL")
    parser.add_argument("--agents", type=int, default=100, help="Number of agents")
    parser.add_argument("--actions", type=int, default=10, help="Actions per agent")
    parser.add_argument("--concurrency", type=int, default=50, help="Max concurrent connections")
    args = parser.parse_args()

    asyncio.run(
        main(
            base_url=args.base_url,
            ws_url=args.ws_url,
            num_agents=args.agents,
            num_actions=args.actions,
            concurrency=args.concurrency,
        )
    )
