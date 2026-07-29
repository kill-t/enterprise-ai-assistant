from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """数据库连接配置。"""

    url: str


class RedisSettings(BaseModel):
    """Redis 连接配置。"""

    url: str


class ProviderSettings(BaseModel):
    """单个大模型 Provider 的访问配置。"""

    api_key: str
    base_url: str
    model: str


class AuthSettings(BaseModel):
    """JWT 签名算法、密钥和 Access Token 有效期配置。"""

    secret_key: SecretStr | None = None
    algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)


class Settings(BaseSettings):
    """聚合应用运行所需的环境变量配置。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Enterprise AI Assistant"
    app_version: str = "0.1.0"

    database: DatabaseSettings
    redis: RedisSettings
    auth: AuthSettings = AuthSettings()

    default_provider: str = "deepseek"
    deepseek: ProviderSettings
    openai: ProviderSettings


@lru_cache
def get_settings() -> Settings:
    """创建并缓存全局配置，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
