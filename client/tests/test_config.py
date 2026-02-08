"""Tests for agent configuration loading and validation."""

import tempfile

import pytest
import yaml

from agentburg_client.config import AgentConfig, LLMConfig, PersonalityConfig, ServerConfig, load_config

# ---------- ServerConfig validation ----------


class TestServerConfig:
    """Test server config field validators."""

    def test_valid_ws_url(self):
        cfg = ServerConfig(url="ws://localhost:8000/ws")
        assert cfg.url == "ws://localhost:8000/ws"

    def test_valid_wss_url(self):
        cfg = ServerConfig(url="wss://api.example.com/ws")
        assert cfg.url.startswith("wss://")

    def test_invalid_url_scheme_rejected(self):
        with pytest.raises(ValueError, match="ws://"):
            ServerConfig(url="http://localhost:8000/ws")

    def test_empty_url_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            ServerConfig(url="")

    def test_max_delay_must_exceed_base(self):
        with pytest.raises(ValueError, match="max_reconnect_delay"):
            ServerConfig(
                url="ws://localhost:8000/ws",
                reconnect_delay=10.0,
                max_reconnect_delay=5.0,
            )

    def test_url_whitespace_stripped(self):
        cfg = ServerConfig(url="  ws://localhost:8000/ws  ")
        assert not cfg.url.startswith(" ")


# ---------- LLMConfig validation ----------


class TestLLMConfig:
    """Test LLM config field validators."""

    def test_known_provider_accepted(self):
        cfg = LLMConfig(provider="openai")
        assert cfg.provider == "openai"

    def test_unknown_provider_warns_but_passes(self):
        # Unknown providers are allowed with a warning
        cfg = LLMConfig(provider="my_custom_llm")
        assert cfg.provider == "my_custom_llm"

    def test_provider_normalized_to_lowercase(self):
        cfg = LLMConfig(provider="OpenAI")
        assert cfg.provider == "openai"

    def test_empty_model_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            LLMConfig(model="")

    def test_long_model_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            LLMConfig(model="a" * 300)

    def test_invalid_api_base_rejected(self):
        with pytest.raises(ValueError, match="http://"):
            LLMConfig(api_base="ftp://invalid")

    def test_valid_api_base_accepted(self):
        cfg = LLMConfig(api_base="https://api.openai.com")
        assert cfg.api_base == "https://api.openai.com"

    def test_empty_api_base_allowed(self):
        cfg = LLMConfig(api_base="")
        assert cfg.api_base == ""

    def test_temperature_bounds(self):
        with pytest.raises(ValueError):
            LLMConfig(temperature=3.0)
        with pytest.raises(ValueError):
            LLMConfig(temperature=-0.1)

    def test_max_tokens_bounds(self):
        with pytest.raises(ValueError):
            LLMConfig(max_tokens=0)


# ---------- PersonalityConfig validation ----------


class TestPersonalityConfig:
    """Test personality config validation."""

    def test_trait_in_range(self):
        cfg = PersonalityConfig(risk_tolerance=0.0, greed=1.0)
        assert cfg.risk_tolerance == 0.0
        assert cfg.greed == 1.0

    def test_trait_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            PersonalityConfig(risk_tolerance=1.5)
        with pytest.raises(ValueError):
            PersonalityConfig(aggression=-0.1)

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            PersonalityConfig(name="")

    def test_long_name_rejected(self):
        with pytest.raises(ValueError):
            PersonalityConfig(name="x" * 100)


# ---------- AgentConfig ----------


class TestAgentConfig:
    """Test complete agent config validation."""

    def test_default_config_valid(self):
        cfg = AgentConfig()
        assert cfg.memory_size == 500
        assert cfg.log_level == "INFO"

    def test_log_level_normalized(self):
        cfg = AgentConfig(log_level="debug")
        assert cfg.log_level == "DEBUG"

    def test_invalid_log_level_rejected(self):
        with pytest.raises(ValueError, match="log_level"):
            AgentConfig(log_level="TRACE")

    def test_invalid_log_format_rejected(self):
        with pytest.raises(ValueError, match="log_format"):
            AgentConfig(log_format="xml")


# ---------- load_config from YAML ----------


class TestLoadConfig:
    """Test loading config from YAML files."""

    def test_load_from_valid_yaml(self):
        data = {
            "server": {"url": "ws://example.com/ws", "token": "abc123"},
            "llm": {"provider": "openai", "model": "gpt-4o-mini"},
            "personality": {"name": "Trader", "title": "Merchant"},
            "memory_size": 200,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name

        cfg = load_config(path)
        assert cfg.server.url == "ws://example.com/ws"
        assert cfg.llm.model == "gpt-4o-mini"
        assert cfg.personality.name == "Trader"
        assert cfg.memory_size == 200

    def test_load_nonexistent_file_returns_defaults(self):
        cfg = load_config("/tmp/does_not_exist_agentburg_test.yaml")
        assert cfg.personality.name == "Agent"  # default
        assert cfg.memory_size == 500

    def test_load_empty_yaml_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            path = f.name

        cfg = load_config(path)
        assert cfg.personality.name == "Agent"
