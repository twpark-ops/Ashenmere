"""Agent brain configuration loaded from YAML config file."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "ollama"  # ollama, openai, anthropic, etc.
    model: str = "llama3.2:3b"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.7
    max_tokens: int = 500


class PersonalityConfig(BaseModel):
    """Agent personality traits that influence decision-making."""

    name: str = "Agent"
    title: str = "Merchant"
    bio: str = "A resourceful trader in the AgentBurg world."
    risk_tolerance: float = 0.5  # 0.0 = very conservative, 1.0 = reckless
    aggression: float = 0.3  # 0.0 = peaceful, 1.0 = aggressive
    sociability: float = 0.5  # 0.0 = loner, 1.0 = social butterfly
    greed: float = 0.5  # 0.0 = generous, 1.0 = greedy
    honesty: float = 0.7  # 0.0 = deceptive, 1.0 = honest
    goals: list[str] = []  # High-level goals like "become the richest agent"


class ServerConfig(BaseModel):
    """Server connection settings."""

    url: str = "ws://localhost:8000/ws"
    token: str = ""  # Agent API token from server registration
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10


class AgentConfig(BaseModel):
    """Complete agent configuration."""

    server: ServerConfig = ServerConfig()
    llm: LLMConfig = LLMConfig()
    personality: PersonalityConfig = PersonalityConfig()
    memory_size: int = 500  # Max memories to keep
    decision_interval: float = 1.0  # Seconds between decisions


def load_config(path: str | Path = "config.yaml") -> AgentConfig:
    """Load agent config from YAML file, falling back to defaults."""
    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return AgentConfig.model_validate(data)
    return AgentConfig()
