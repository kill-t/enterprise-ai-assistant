import math
from collections.abc import Sequence

from app.infrastructure.embedding.base import BaseEmbeddingProvider
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.schemas import EmbeddingRequest, EmbeddingResult


class EmbeddingService:
    """统一处理输入校验、顺序拆批和 Provider 响应边界。"""

    def __init__(
        self,
        provider: BaseEmbeddingProvider,
        batch_size: int,
    ) -> None:
        """绑定一个配置快照对应的 Provider 和正数批次大小。"""
        if batch_size <= 0:
            raise EmbeddingError(
                code=EmbeddingErrorCode.CONFIGURATION_INVALID,
                message="Embedding 批次大小必须大于零",
            )
        self.provider = provider
        self.profile = provider.profile
        self.batch_size = batch_size

    async def embed_texts(self, texts: Sequence[str]) -> EmbeddingResult:
        """按输入顺序分批生成向量，并在汇总前校验每个 Provider 响应。"""
        normalized_texts = tuple(texts)
        if not normalized_texts or any(not text.strip() for text in normalized_texts):
            raise EmbeddingError(
                code=EmbeddingErrorCode.INVALID_INPUT,
                message="Embedding 输入不能为空或只包含空白字符",
            )

        vectors: list[tuple[float, ...]] = []
        total_input_tokens = 0
        for start in range(0, len(normalized_texts), self.batch_size):
            batch = normalized_texts[start : start + self.batch_size]
            result = await self.provider.embed(EmbeddingRequest(texts=batch))
            self._validate_result(result=result, expected_count=len(batch))
            vectors.extend(result.vectors)
            total_input_tokens += result.input_tokens

        return EmbeddingResult(
            vectors=tuple(vectors),
            model=self.profile.model,
            dimension=self.profile.dimension,
            input_tokens=total_input_tokens,
        )

    def _validate_result(
        self,
        result: EmbeddingResult,
        expected_count: int,
    ) -> None:
        """阻止模型、数量、维度或数值异常的向量污染 pgvector。"""
        if result.model != self.profile.model:
            raise EmbeddingError(
                code=EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
                message="Embedding 响应模型与知识库配置不一致",
            )
        if result.dimension != self.profile.dimension:
            raise EmbeddingError(
                code=EmbeddingErrorCode.DIMENSION_MISMATCH,
                message="Embedding 响应维度与知识库配置不一致",
            )
        if len(result.vectors) != expected_count:
            raise EmbeddingError(
                code=EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
                message="Embedding 响应数量与输入数量不一致",
            )

        for vector in result.vectors:
            if len(vector) != self.profile.dimension:
                raise EmbeddingError(
                    code=EmbeddingErrorCode.DIMENSION_MISMATCH,
                    message="Embedding 向量长度与知识库配置不一致",
                )
            if any(not math.isfinite(value) for value in vector):
                raise EmbeddingError(
                    code=EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
                    message="Embedding 向量包含非有限数值",
                )
