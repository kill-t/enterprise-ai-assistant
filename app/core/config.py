from functools import lru_cache

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
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Enterprise AI Assistant"
    app_version: str = "0.1.0"
    debug: bool = True

    database: DatabaseSettings
    redis: RedisSettings

    default_provider: str = "deepseek"
    deepseek: ProviderSettings
    openai: ProviderSettings


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
