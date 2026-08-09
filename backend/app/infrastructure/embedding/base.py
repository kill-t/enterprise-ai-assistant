from abc import ABC, abstractmethod

from app.infrastructure.embedding.schemas import (
    EmbeddingProfile,
    EmbeddingRequest,
    EmbeddingResult,
)


class BaseEmbeddingProvider(ABC):
    """定义所有 Embedding 厂商适配器必须实现的最小接口。"""

    def __init__(self, profile: EmbeddingProfile) -> None:
        """保存本 Provider 只能产生的模型和维度配置。"""
        self.profile = profile

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """按输入顺序生成向量，不负责拆批或数据库写入。"""
        raise NotImplementedError
