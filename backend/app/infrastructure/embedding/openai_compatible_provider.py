from typing import Any

from openai import AsyncOpenAI

from app.core.loggers import llm_logger
from app.infrastructure.embedding.base import BaseEmbeddingProvider
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.schemas import (
    EmbeddingProfile,
    EmbeddingRequest,
    EmbeddingResult,
)


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """通过 OpenAI 异步 SDK 调用兼容 Embedding 接口。"""

    def __init__(
        self,
        profile: EmbeddingProfile,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """支持生产创建真实客户端，也允许测试注入无网络的假客户端。"""
        super().__init__(profile)
        if client is not None:
            self.client = client
            return
        if not api_key:
            raise EmbeddingError(
                code=EmbeddingErrorCode.CONFIGURATION_INVALID,
                message="未配置真实 Embedding Provider 的 API Key",
            )
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """调用兼容接口，并按响应 index 恢复与请求一致的向量顺序。"""
        try:
            response = await self.client.embeddings.create(
                model=self.profile.model,
                input=list(request.texts),
                dimensions=self.profile.dimension,
                encoding_format="float",
            )
        except Exception as exc:
            # 只在服务端日志保留堆栈，对 Worker 和 API 返回稳定的安全错误信息。
            llm_logger.warning(
                "Embedding 模型调用失败 provider=%s model=%s reason=%s",
                self.profile.provider,
                self.profile.model,
                type(exc).__name__,
                exc_info=True,
            )
            raise EmbeddingError(
                code=EmbeddingErrorCode.PROVIDER_REQUEST_FAILED,
                message="Embedding 模型调用失败",
            ) from exc

        try:
            ordered_items = sorted(response.data, key=lambda item: item.index)
            indices = [item.index for item in ordered_items]
            if indices != list(range(len(request.texts))):
                raise ValueError("响应 index 不连续")
            vectors = tuple(
                tuple(float(value) for value in item.embedding)
                for item in ordered_items
            )
            usage = getattr(response, "usage", None)
            input_tokens = int(
                getattr(usage, "prompt_tokens", None)
                or getattr(usage, "total_tokens", 0)
                or 0
            )
            model = getattr(response, "model", None) or self.profile.model
        except (AttributeError, TypeError, ValueError) as exc:
            llm_logger.warning(
                "Embedding 模型响应协议无效 provider=%s model=%s reason=%s",
                self.profile.provider,
                self.profile.model,
                type(exc).__name__,
                exc_info=True,
            )
            raise EmbeddingError(
                code=EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
                message="Embedding 模型响应格式无效",
            ) from exc

        return EmbeddingResult(
            vectors=vectors,
            model=model,
            dimension=self.profile.dimension,
            input_tokens=input_tokens,
        )
