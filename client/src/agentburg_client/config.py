"""Agent brain configuration loaded from YAML config file.

Provides validated configuration models using Pydantic v2 with field validators,
model-level validators, and sensible defaults for all settings.
"""

import logging
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "ollama"  # ollama, openai, anthropic, etc.
    model: str = "llama3.2:3b"
    api_key: str = ""
    api_base: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=500, ge=1, le=128_000)
    timeout: float = Field(default=30.0, gt=0.0, description="LLM call timeout in seconds")
    max_retries: int = Field(
        default=3, ge=0, le=10,
        description="Max retries for transient LLM failures",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Ensure provider is a known LLM provider string."""
        allowed = {
            "ollama", "openai", "anthropic", "azure", "cohere",
            "huggingface", "replicate", "together_ai", "bedrock",
            "vertex_ai", "groq", "deepseek", "mistral",
        }
        v = v.strip().lower()
        if v not in allowed:
            logger.warning(
                "LLM provider '%s' is not in the known set %s. "
                "Proceeding anyway — LiteLLM may still support it.",
                v, sorted(allowed),
            )
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """Ensure model name is non-empty and has reasonable format."""
        v = v.strip()
        if not v:
            raise ValueError("LLM model name must not be empty")
        if len(v) > 256:
            raise ValueError(f"LLM model name too long ({len(v)} chars, max 256)")
        return v

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, v: str) -> str:
        """Validate API base URL format when provided."""
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(
                f"api_base must start with http:// or https://, got: '{v}'"
            )
        return v


class PersonalityConfig(BaseModel):
    """Agent personality traits that influence decision-making."""

    name: str = Field(default="Agent", min_length=1, max_length=64)
    title: str = Field(default="Merchant", min_length=1, max_length=64)
    bio: str = Field(
        default="A resourceful trader in the AgentBurg world.",
        max_length=1024,
    )
    risk_tolerance: float = Field(default=0.5, ge=0.0, le=1.0)
    aggression: float = Field(default=0.3, ge=0.0, le=1.0)
    sociability: float = Field(default=0.5, ge=0.0, le=1.0)
    greed: float = Field(default=0.5, ge=0.0, le=1.0)
    honesty: float = Field(default=0.7, ge=0.0, le=1.0)
    goals: list[str] = Field(default_factory=list)


class ServerConfig(BaseModel):
    """Server connection settings."""

    url: str = "ws://localhost:8000/ws"
    token: str = ""  # Agent API token from server registration
    reconnect_delay: float = Field(
        default=1.0, gt=0.0, le=300.0,
        description="Base reconnection delay in seconds (used for exponential backoff)",
    )
    max_reconnect_delay: float = Field(
        default=60.0, gt=0.0, le=600.0,
        description="Maximum reconnection delay in seconds",
    )
    max_reconnect_attempts: int = Field(
        default=0, ge=0,
        description="Max reconnect attempts (0 = unlimited)",
    )
    heartbeat_interval: float = Field(
        default=30.0, gt=0.0, le=300.0,
        description="Ping-pong heartbeat interval in seconds",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure WebSocket URL has correct scheme."""
        v = v.strip()
        if not v:
            raise ValueError("Server URL must not be empty")
        if not v.startswith(("ws://", "wss://")):
            raise ValueError(
                f"Server URL must start with ws:// or wss://, got: '{v}'"
            )
        return v

    @model_validator(mode="after")
    def validate_delays(self) -> Self:
        """Ensure max_reconnect_delay >= reconnect_delay."""
        if self.max_reconnect_delay < self.reconnect_delay:
            raise ValueError(
                f"max_reconnect_delay ({self.max_reconnect_delay}s) must be >= "
                f"reconnect_delay ({self.reconnect_delay}s)"
            )
        return self


class AgentConfig(BaseModel):
    """Complete agent configuration."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    memory_size: int = Field(default=500, ge=10, le=100_000)
    memory_db_path: str = Field(
        default="",
        description="Path to SQLite memory database. Empty string = in-memory only.",
    )
    decision_interval: float = Field(default=1.0, ge=0.1, le=300.0)
    log_format: str = Field(
        default="text",
        description="Logging format: 'text' for human-readable, 'json' for structured",
    )
    log_level: str = Field(default="INFO")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log level is valid."""
        v = v.strip().upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in valid_levels:
            raise ValueError(f"log_level must be one of {sorted(valid_levels)}, got: '{v}'")
        return v

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Ensure log format is valid."""
        v = v.strip().lower()
        if v not in {"text", "json"}:
            raise ValueError(f"log_format must be 'text' or 'json', got: '{v}'")
        return v

    @model_validator(mode="after")
    def validate_auth(self) -> Self:
        """Warn if token is empty — connection will likely fail."""
        if not self.server.token:
            logger.warning(
                "server.token is empty — authentication will likely fail. "
                "Register your agent at the server first."
            )
        return self


def load_config(path: str | Path = "config.yaml") -> AgentConfig:
    """Load agent config from YAML file, falling back to defaults.

    Args:
        path: Path to a YAML configuration file.

    Returns:
        Validated AgentConfig instance.
    """
    config_path = Path(path)
    if config_path.exists():
        logger.info("Loading config from %s", config_path.resolve())
        with open(config_path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        return AgentConfig.model_validate(data)

    logger.warning("Config file %s not found, using defaults", config_path)
    return AgentConfig()
