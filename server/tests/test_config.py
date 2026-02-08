"""Tests for client agent configuration."""

import tempfile
from pathlib import Path

from agentburg_client.config import AgentConfig, load_config


def test_default_config():
    config = AgentConfig()

    assert config.server.url == "ws://localhost:8000/ws"
    assert config.llm.provider == "ollama"
    assert config.llm.model == "llama3.2:3b"
    assert config.personality.name == "Agent"
    assert config.memory_size == 500


def test_load_from_yaml():
    yaml_content = """
server:
  url: "ws://example.com/ws"
  token: "test-token"
llm:
  provider: "openai"
  model: "gpt-4o"
  api_key: "sk-test"
personality:
  name: "TestBot"
  title: "Tester"
  risk_tolerance: 0.9
  goals:
    - "Run all tests"
    - "Find bugs"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        config = load_config(f.name)

    assert config.server.url == "ws://example.com/ws"
    assert config.server.token == "test-token"
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o"
    assert config.personality.name == "TestBot"
    assert config.personality.risk_tolerance == 0.9
    assert len(config.personality.goals) == 2

    Path(f.name).unlink()


def test_load_missing_file():
    config = load_config("/nonexistent/path.yaml")
    # Should return defaults
    assert config.personality.name == "Agent"
    assert config.llm.provider == "ollama"
