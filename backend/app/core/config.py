from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str


class RedisSettings(BaseModel):
    url: str


class ProviderSettings(BaseModel):
    api_key: str
    base_url: str
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Enterprise AI Assistant"
    app_version: str = "0.1.0"

    database: DatabaseSettings
    redis: RedisSettings

    default_provider: str = "deepseek"
    deepseek: ProviderSettings
    openai: ProviderSettings


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
