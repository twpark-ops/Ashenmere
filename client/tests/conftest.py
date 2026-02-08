"""Shared fixtures for client tests."""

import pytest

from agentburg_client.config import AgentConfig, LLMConfig, PersonalityConfig, ServerConfig


@pytest.fixture
def default_config() -> AgentConfig:
    """Provide a minimal config for unit tests (no real server/LLM needed)."""
    return AgentConfig(
        server=ServerConfig(
            url="ws://localhost:8000/ws",
            token="test-token-abc123",
            reconnect_delay=0.1,
            max_reconnect_delay=0.5,
            max_reconnect_attempts=3,
            heartbeat_interval=5.0,
        ),
        llm=LLMConfig(
            provider="ollama",
            model="llama3.2:3b",
            timeout=5.0,
            max_retries=1,
        ),
        personality=PersonalityConfig(
            name="TestBot",
            title="Test Merchant",
            bio="A test agent for unit testing.",
            risk_tolerance=0.5,
            aggression=0.3,
            sociability=0.5,
            greed=0.5,
            honesty=0.7,
            goals=["Make money", "Survive"],
        ),
        memory_size=100,
        memory_db_path="",
        decision_interval=1.0,
    )
