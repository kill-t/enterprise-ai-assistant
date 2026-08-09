import hashlib
import math
import re

from app.infrastructure.embedding.base import BaseEmbeddingProvider
from app.infrastructure.embedding.schemas import EmbeddingRequest, EmbeddingResult
from app.infrastructure.llm.token_counter import TokenCounter


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """使用稳定词项哈希生成本地向量，供测试和无额度演示使用。"""

    _WORD_PATTERN = re.compile(r"[a-z0-9]+")
    _CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """为每条文本生成固定维度的 L2 归一化稀疏哈希向量。"""
        vectors = tuple(self._embed_text(text) for text in request.texts)
        # Mock 模型没有真实 usage，使用保守估算便于测试预算和监控字段。
        token_counter = TokenCounter(self.profile.model, encoder=False)
        input_tokens = sum(token_counter.count(text) for text in request.texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.profile.model,
            dimension=self.profile.dimension,
            input_tokens=input_tokens,
        )

    def _embed_text(self, text: str) -> tuple[float, ...]:
        """把文本特征稳定映射到向量槽位，并处理哈希碰撞。"""
        vector = [0.0] * self.profile.dimension
        features = self._extract_features(text)
        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], byteorder="big") % self.profile.dimension
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            # 正常入口已经拒绝空白文本；该分支保护直接调用 Provider 的极端输入。
            return tuple(vector)
        return tuple(value / norm for value in vector)

    def _extract_features(self, text: str) -> tuple[str, ...]:
        """提取英文词、中文单字和中文二元词，保留基础词面相似度。"""
        normalized = text.casefold()
        words = [f"word:{word}" for word in self._WORD_PATTERN.findall(normalized)]
        cjk_characters = self._CJK_PATTERN.findall(normalized)
        cjk_unigrams = [f"cjk1:{character}" for character in cjk_characters]
        cjk_bigrams = [
            f"cjk2:{left}{right}"
            for left, right in zip(cjk_characters, cjk_characters[1:])
        ]
        features = words + cjk_unigrams + cjk_bigrams
        if features:
            return tuple(features)

        # 表情或其他文字没有命中上述规则时，仍按可见字符产生稳定向量。
        return tuple(
            f"char:{character}" for character in normalized if not character.isspace()
        )
