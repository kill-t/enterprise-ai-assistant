from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """数据库连接配置。"""

    url: str


class RedisSettings(BaseModel):
    """Redis 连接配置。"""

    url: str


class ModelContextSettings(BaseModel):
    """描述模型上下文窗口，以及输入预算必须预留的空间。"""

    context_window_tokens: int = Field(default=8192, gt=0)
    max_output_tokens: int = Field(default=1024, gt=0)
    token_safety_margin: int = Field(default=256, ge=0)

    @model_validator(mode="after")
    def validate_token_budget(self) -> "ModelContextSettings":
        """保证系统始终拥有大于零的输入预算。"""
        if self.max_output_tokens + self.token_safety_margin >= self.context_window_tokens:
            raise ValueError("输出预留与安全余量必须小于模型上下文窗口")
        return self

    @property
    def available_input_tokens(self) -> int:
        """返回扣除模型输出预留和安全余量后的输入上限。"""
        return (
            self.context_window_tokens
            - self.max_output_tokens
            - self.token_safety_margin
        )


class ProviderSettings(ModelContextSettings):
    """单个大模型 Provider 的访问配置。"""

    api_key: str
    base_url: str
    model: str


class AuthSettings(BaseModel):
    """JWT 签名算法、密钥和 Access Token 有效期配置。"""

    secret_key: SecretStr | None = None
    algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)


class WorkerSettings(BaseModel):
    """Celery Worker 的 Broker、队列和任务超时配置。"""

    broker_url: str = "redis://localhost:6379/1"
    default_queue: str = "conversation-summary"
    task_soft_time_limit_seconds: int = Field(default=270, gt=0)
    task_time_limit_seconds: int = Field(default=300, gt=0)
    summary_retry_base_delay_seconds: int = Field(default=30, gt=0)
    summary_retry_max_delay_seconds: int = Field(default=300, gt=0)

    @model_validator(mode="after")
    def validate_time_limits(self) -> "WorkerSettings":
        """保证软超时早于硬超时，使任务有机会保存失败状态。"""
        if self.task_soft_time_limit_seconds >= self.task_time_limit_seconds:
            raise ValueError("Worker 软超时必须小于硬超时")
        if (
            self.summary_retry_base_delay_seconds
            > self.summary_retry_max_delay_seconds
        ):
            raise ValueError("摘要重试基础延迟不能大于最大退避时间")
        return self


class ConversationMemorySettings(BaseModel):
    """长会话摘要触发和近期历史保留的初始参数。"""

    summary_trigger_tokens: int = Field(default=3000, gt=0)
    recent_history_budget_tokens: int = Field(default=2000, gt=0)
    summary_batch_tokens: int = Field(default=4000, gt=0)


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
    worker: WorkerSettings = WorkerSettings()
    conversation_memory: ConversationMemorySettings = ConversationMemorySettings()

    default_provider: str = "deepseek"
    deepseek: ProviderSettings
    openai: ProviderSettings


@lru_cache
def get_settings() -> Settings:
    """创建并缓存全局配置，避免重复解析环境变量。"""
    return Settings()


settings = get_settings()
