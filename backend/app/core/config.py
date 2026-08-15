from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """数据库连接配置。"""

    url: str


class RedisSettings(BaseModel):
    """Redis 连接配置。"""

    url: str


class ObjectStorageSettings(BaseModel):
    """MinIO 连接、存储桶和文档上传大小配置。"""

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: SecretStr = SecretStr("minioadmin")
    secure: bool = False
    bucket_name: str = "enterprise-ai-documents"
    max_file_size_bytes: int = Field(default=20 * 1024 * 1024, gt=0)


class EmbeddingSettings(BaseModel):
    """控制默认 Embedding Provider、模型、维度和批处理边界。"""

    provider: Literal["mock", "openai_compatible"] = "mock"
    api_key: SecretStr | None = None
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "text-embedding-v4"
    mock_model: str = "mock-hash-1024"
    # 环境变量以字符串进入配置层，先按 int 解析，再由校验器阻止维度漂移。
    dimension: int = Field(default=1024, gt=0)
    # text-embedding-v4 官方同步接口单批最多接受 10 条文本。
    batch_size: int = Field(default=10, gt=0, le=10)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("dimension")
    @classmethod
    def validate_database_dimension(cls, value: int) -> int:
        """第一版数据库列固定为 vector(1024)，其他维度必须重新迁移后才能使用。"""
        if value != 1024:
            raise ValueError("Embedding 维度必须与数据库 vector(1024) 一致")
        return value


class RagSettings(BaseModel):
    """控制文档分块以及向量检索的候选与可靠性边界。"""

    chunk_size_tokens: int = Field(default=600, gt=0)
    chunk_overlap_tokens: int = Field(default=80, ge=0)
    # 不同 Embedding 的分数分布不可直接比较，阈值必须按 Provider 分开校准。
    mock_similarity_threshold: float = Field(default=0.2, ge=-1.0, le=1.0)
    openai_compatible_similarity_threshold: float = Field(
        default=0.45,
        ge=-1.0,
        le=1.0,
    )
    search_candidate_multiplier: int = Field(default=4, ge=1, le=20)
    # 默认继续使用无远程费用的阈值方案；只有显式切换才组装 LLM Selector。
    evidence_selector: Literal["threshold", "llm"] = "threshold"
    llm_evidence_selector_model: str = "deepseek-v4-flash"
    llm_evidence_selector_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_evidence_selector_thinking_enabled: bool = False
    llm_evidence_selector_max_output_retries: int = Field(default=1, ge=0, le=3)

    @field_validator("llm_evidence_selector_model")
    @classmethod
    def normalize_selector_model(cls, value: str) -> str:
        """清理并拒绝空模型名，避免启用灰度后把错误拖到第一次请求。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM 证据选择模型名不能为空")
        return normalized

    def similarity_threshold_for(self, provider: str) -> float:
        """根据知识库冻结的 Provider 返回经过独立评测的可靠性阈值。"""
        if provider == "mock":
            return self.mock_similarity_threshold
        if provider == "openai_compatible":
            return self.openai_compatible_similarity_threshold
        raise ValueError("不支持该 Embedding Provider 的检索阈值")

    def validate_runtime(self, *, deepseek_api_key: str) -> None:
        """启用 LLM Selector 时在应用启动阶段校验真实 DeepSeek 密钥。"""
        if self.evidence_selector != "llm":
            return
        normalized_key = deepseek_api_key.strip()
        if not normalized_key or normalized_key == "replace-with-deepseek-key":
            raise RuntimeError(
                "RAG 已启用 LLM 证据选择，但未配置有效的 DEEPSEEK__API_KEY"
            )

    @model_validator(mode="after")
    def validate_chunk_budget(self) -> "RagSettings":
        """保证重叠小于单个 Chunk 上限，避免分块无法前进。"""
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("Chunk 重叠 Token 必须小于 Chunk Token 上限")
        return self


class ModelContextSettings(BaseModel):
    """描述模型上下文窗口，以及输入预算必须预留的空间。"""

    context_window_tokens: int = Field(default=8192, gt=0)
    max_output_tokens: int = Field(default=1024, gt=0)
    token_safety_margin: int = Field(default=256, ge=0)

    @model_validator(mode="after")
    def validate_token_budget(self) -> "ModelContextSettings":
        """保证系统始终拥有大于零的输入预算。"""
        if (
            self.max_output_tokens + self.token_safety_margin
            >= self.context_window_tokens
        ):
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

    def validate_runtime(self) -> None:
        """在服务启动前校验 JWT 密钥，避免首次登录才暴露配置缺失。"""
        if self.secret_key is None:
            raise RuntimeError("未配置 AUTH__SECRET_KEY，无法启动需要身份认证的服务")

        # HS256 密钥应至少达到 32 字节；按 UTF-8 字节数而非字符数判断更准确。
        secret_key_bytes = self.secret_key.get_secret_value().encode("utf-8")
        if len(secret_key_bytes) < 32:
            raise RuntimeError("AUTH__SECRET_KEY 至少 32 字节，请使用安全随机值")


class WorkerSettings(BaseModel):
    """Celery Worker 的 Broker、队列和任务超时配置。"""

    broker_url: str = "redis://localhost:6379/1"
    default_queue: str = "conversation-summary"
    task_soft_time_limit_seconds: int = Field(default=270, gt=0)
    task_time_limit_seconds: int = Field(default=300, gt=0)
    summary_retry_base_delay_seconds: int = Field(default=30, gt=0)
    summary_retry_max_delay_seconds: int = Field(default=300, gt=0)
    summary_recovery_interval_seconds: int = Field(default=60, gt=0)
    summary_running_timeout_seconds: int = Field(default=600, gt=0)
    summary_dispatch_lease_seconds: int = Field(default=120, gt=0)
    summary_recovery_batch_size: int = Field(default=100, gt=0, le=1000)
    document_ingestion_queue: str = "document-ingestion"
    document_retry_base_delay_seconds: int = Field(default=30, gt=0)
    document_retry_max_delay_seconds: int = Field(default=300, gt=0)
    document_recovery_interval_seconds: int = Field(default=60, gt=0)
    document_running_timeout_seconds: int = Field(default=900, gt=0)
    document_dispatch_lease_seconds: int = Field(default=120, gt=0)
    document_recovery_batch_size: int = Field(default=100, gt=0, le=1000)

    @model_validator(mode="after")
    def validate_time_limits(self) -> "WorkerSettings":
        """保证软超时早于硬超时，使任务有机会保存失败状态。"""
        if self.task_soft_time_limit_seconds >= self.task_time_limit_seconds:
            raise ValueError("Worker 软超时必须小于硬超时")
        if self.summary_retry_base_delay_seconds > self.summary_retry_max_delay_seconds:
            raise ValueError("摘要重试基础延迟不能大于最大退避时间")
        if (
            self.document_retry_base_delay_seconds
            > self.document_retry_max_delay_seconds
        ):
            raise ValueError("文档入库重试基础延迟不能大于最大退避时间")
        if self.summary_running_timeout_seconds <= self.task_time_limit_seconds:
            raise ValueError("摘要运行超时恢复阈值必须晚于 Celery 硬超时")
        if self.summary_dispatch_lease_seconds < self.summary_recovery_interval_seconds:
            raise ValueError("摘要投递租约不能短于恢复扫描周期")
        if self.document_running_timeout_seconds <= self.task_time_limit_seconds:
            raise ValueError("文档运行超时恢复阈值必须晚于 Celery 硬超时")
        if (
            self.document_dispatch_lease_seconds
            < self.document_recovery_interval_seconds
        ):
            raise ValueError("文档投递租约不能短于恢复扫描周期")
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
    object_storage: ObjectStorageSettings = ObjectStorageSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    rag: RagSettings = RagSettings()
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
