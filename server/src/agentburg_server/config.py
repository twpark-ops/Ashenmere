"""Server configuration via environment variables."""

import logging
import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_DEFAULTS = {
    "jwt_secret_key": "change-me-to-a-random-secret-key",
    "admin_password": "change-me",
}


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Database
    database_url: str = "postgresql+asyncpg://agentburg:agentburg@localhost:5432/agentburg"

    # Security
    jwt_secret_key: str = "change-me-to-a-random-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = False

    # NATS
    nats_url: str = "nats://localhost:4222"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # World simulation
    # Pacing: 1 real hour = 1 sim day (6 macro ticks × 10 min)
    # Users check ~3 times/day → 8 sim days between checks → plenty of drama
    tick_interval_seconds: float = 2.0  # legacy fallback
    ticks_per_day: int = 6  # macro ticks per simulated day
    macro_tick_seconds: float = 600.0  # 10 min between economic ticks
    micro_tick_seconds: float = 30.0  # 30 sec between ambient ticks
    initial_agent_balance: int = 10000  # In cents ($100.00)

    # Seasons
    season_duration_days: int = 168  # simulated days per season (= 7 real days)
    season_max_agents: int = 50

    # CORS (production origins)
    cors_origins: list[str] = []

    # NPC agents

    # Rate limiting
    ws_rate_limit_per_second: int = 10
    api_rate_limit_per_minute: int = 60

    # Dashboard
    dashboard_api_key: str = ""  # Empty = no auth required (backwards compatible)

    # Admin
    admin_email: str = "admin@agentburg.world"
    admin_password: str = "change-me"

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "Settings":
        """Emit warnings when insecure default values are used in non-debug mode."""
        if not self.debug:
            for field, default_val in _INSECURE_DEFAULTS.items():
                if getattr(self, field) == default_val:
                    warnings.warn(
                        f"**SECURITY** {field} is using the insecure default. "
                        f"Set the {field.upper()} environment variable before production deployment.",
                        stacklevel=2,
                    )
        return self


settings = Settings()
