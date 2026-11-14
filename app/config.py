"""Application settings. Read from environment variables only (see .env.example)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+psycopg://billing:billing@localhost:5433/billing"
    test_database_url: str = "postgresql+psycopg://billing:billing@localhost:5433/billing_test"
    public_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # Stripe — test mode only. SecretStr keeps values out of reprs and logs.
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_pro_price_id: str = ""

    worker_poll_seconds: float = 1.0

    # Pinned pricing constants (docs/pricing.md). Relative paths resolve from the repo root.
    pricing_config_path: str = "config/pricing.toml"

    # Usage alerts (80% / 100%). Empty URL = alerts go to the log only.
    alert_webhook_url: str = ""
    alert_webhook_secret: SecretStr = SecretStr("")


@lru_cache
def get_settings() -> Settings:
    return Settings()
