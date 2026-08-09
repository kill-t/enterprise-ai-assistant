from app.core.config import EmbeddingSettings, settings
from app.infrastructure.embedding.base import BaseEmbeddingProvider
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.mock_provider import MockEmbeddingProvider
from app.infrastructure.embedding.openai_compatible_provider import (
    OpenAICompatibleEmbeddingProvider,
)
from app.infrastructure.embedding.schemas import EmbeddingProfile
from app.infrastructure.embedding.service import EmbeddingService


class EmbeddingProviderFactory:
    """根据全局默认配置或知识库快照创建匹配的 Provider。"""

    def __init__(self, embedding_settings: EmbeddingSettings | None = None) -> None:
        """允许生产读取全局配置，也允许测试显式注入独立配置。"""
        self.settings = embedding_settings or settings.embedding

    def default_profile(self) -> EmbeddingProfile:
        """返回新建知识库应冻结的当前默认配置快照。"""
        provider = self.settings.provider
        model = self.settings.mock_model if provider == "mock" else self.settings.model
        return EmbeddingProfile(
            provider=provider,
            model=model,
            dimension=self.settings.dimension,
        )

    def create_provider(self, profile: EmbeddingProfile) -> BaseEmbeddingProvider:
        """按知识库快照创建 Provider，避免切换默认配置后混用旧向量。"""
        if profile.provider == "mock":
            return MockEmbeddingProvider(profile)
        if profile.provider == "openai_compatible":
            api_key = self.settings.api_key
            return OpenAICompatibleEmbeddingProvider(
                profile=profile,
                api_key=api_key.get_secret_value() if api_key is not None else None,
                base_url=self.settings.base_url,
                timeout_seconds=self.settings.timeout_seconds,
                max_retries=self.settings.max_retries,
            )
        raise EmbeddingError(
            code=EmbeddingErrorCode.CONFIGURATION_INVALID,
            message="不支持该 Embedding Provider",
        )

    def create_service(
        self,
        profile: EmbeddingProfile | None = None,
    ) -> EmbeddingService:
        """组装指定快照的 Provider 与统一批处理 Service。"""
        resolved_profile = profile or self.default_profile()
        return EmbeddingService(
            provider=self.create_provider(resolved_profile),
            batch_size=self.settings.batch_size,
        )
