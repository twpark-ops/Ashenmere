"""Server configuration via environment variables."""

from pydantic_settings import BaseSettings


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
    tick_interval_seconds: float = 2.0
    ticks_per_day: int = 720
    initial_agent_balance: int = 10000  # In cents ($100.00)

    # Admin
    admin_email: str = "admin@agentburg.world"
    admin_password: str = "change-me"


settings = Settings()
