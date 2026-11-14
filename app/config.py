"""Application settings. Read from environment variables only (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+psycopg://billing:billing@localhost:5433/billing"
    test_database_url: str = "postgresql+psycopg://billing:billing@localhost:5433/billing_test"
    public_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
