import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import EmbeddingSettings
from app.infrastructure.embedding.base import BaseEmbeddingProvider
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.mock_provider import MockEmbeddingProvider
from app.infrastructure.embedding.openai_compatible_provider import (
    OpenAICompatibleEmbeddingProvider,
)
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.embedding.schemas import (
    EmbeddingProfile,
    EmbeddingRequest,
    EmbeddingResult,
)
from app.infrastructure.embedding.service import EmbeddingService


class RecordingEmbeddingProvider(BaseEmbeddingProvider):
    """记录批次并生成容易断言的向量，用于隔离测试 Service 编排。"""

    def __init__(self, profile: EmbeddingProfile) -> None:
        """保存 Provider 配置，并初始化批次调用记录。"""
        super().__init__(profile)
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """把文本末尾数字写入向量首位，以验证输出顺序没有变化。"""
        self.calls.append(request.texts)
        vectors = tuple(
            (float(text[-1]),) + (0.0,) * (self.profile.dimension - 1)
            for text in request.texts
        )
        return EmbeddingResult(
            vectors=vectors,
            model=self.profile.model,
            dimension=self.profile.dimension,
            input_tokens=len(request.texts),
        )


def test_embedding_service_rejects_empty_or_blank_input() -> None:
    """空批次和空白文本应在调用 Provider 前被拒绝。"""
    provider = RecordingEmbeddingProvider(build_profile(dimension=3))
    service = EmbeddingService(provider=provider, batch_size=2)

    for invalid_texts in ([], ["有效文本", "   "]):
        with pytest.raises(EmbeddingError) as error:
            asyncio.run(service.embed_texts(invalid_texts))

        assert error.value.code == EmbeddingErrorCode.INVALID_INPUT

    assert provider.calls == []


def test_mock_embedding_is_stable_normalized_and_has_fixed_dimension() -> None:
    """Mock 向量必须可复现、长度固定且完成 L2 归一化。"""
    profile = build_profile(dimension=1024)
    provider = MockEmbeddingProvider(profile)
    request = EmbeddingRequest(texts=("项目会议纪要", "project meeting notes"))

    first = asyncio.run(provider.embed(request))
    second = asyncio.run(MockEmbeddingProvider(profile).embed(request))

    assert first == second
    assert first.dimension == 1024
    assert all(len(vector) == 1024 for vector in first.vectors)
    assert all(math.isfinite(value) for vector in first.vectors for value in vector)
    assert all(math.isclose(vector_norm(vector), 1.0) for vector in first.vectors)


def test_mock_embedding_preserves_basic_lexical_similarity() -> None:
    """Mock 不是语义模型，但共享词语的文本应比无关文本更接近。"""
    provider = MockEmbeddingProvider(build_profile(dimension=1024))
    result = asyncio.run(
        provider.embed(
            EmbeddingRequest(
                texts=("项目会议安排", "项目会议纪要", "食谱中的番茄炒蛋"),
            )
        )
    )

    related_score = cosine_similarity(result.vectors[0], result.vectors[1])
    unrelated_score = cosine_similarity(result.vectors[0], result.vectors[2])

    assert related_score > unrelated_score


def test_embedding_service_batches_sequentially_and_preserves_order() -> None:
    """Service 应按配置拆批，并把多批结果恢复成原始输入顺序。"""
    provider = RecordingEmbeddingProvider(build_profile(dimension=3))
    service = EmbeddingService(provider=provider, batch_size=2)

    result = asyncio.run(
        service.embed_texts(["文本0", "文本1", "文本2", "文本3", "文本4"])
    )

    assert provider.calls == [
        ("文本0", "文本1"),
        ("文本2", "文本3"),
        ("文本4",),
    ]
    assert [vector[0] for vector in result.vectors] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert result.input_tokens == 5


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (
            EmbeddingResult(
                vectors=((1.0, 0.0),),
                model="wrong-model",
                dimension=2,
            ),
            EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
        ),
        (
            EmbeddingResult(
                vectors=((1.0, 0.0),),
                model="test-model",
                dimension=3,
            ),
            EmbeddingErrorCode.DIMENSION_MISMATCH,
        ),
        (
            EmbeddingResult(
                vectors=((math.nan, 0.0),),
                model="test-model",
                dimension=2,
            ),
            EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
        ),
        (
            EmbeddingResult(
                vectors=(),
                model="test-model",
                dimension=2,
            ),
            EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
        ),
    ],
)
def test_embedding_service_rejects_invalid_provider_response(
    result: EmbeddingResult,
    expected_code: EmbeddingErrorCode,
) -> None:
    """模型、维度、数值或数量异常时不能把不可信向量交给数据库。"""
    profile = build_profile(dimension=2)
    provider = SimpleNamespace(profile=profile, embed=AsyncMock(return_value=result))
    service = EmbeddingService(provider=provider, batch_size=10)

    with pytest.raises(EmbeddingError) as error:
        asyncio.run(service.embed_texts(["文本0"]))

    assert error.value.code == expected_code


def test_openai_compatible_provider_maps_request_and_restores_index_order() -> None:
    """适配器应传递标准参数，并根据厂商 index 恢复批次顺序。"""
    response = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[0.0, 1.0]),
            SimpleNamespace(index=0, embedding=[1.0, 0.0]),
        ],
        model="text-embedding-v4",
        usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
    )
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    profile = EmbeddingProfile(
        provider="openai_compatible",
        model="text-embedding-v4",
        dimension=2,
    )
    provider = OpenAICompatibleEmbeddingProvider(profile=profile, client=client)

    result = asyncio.run(provider.embed(EmbeddingRequest(texts=("第一段", "第二段"))))

    create.assert_awaited_once_with(
        model="text-embedding-v4",
        input=["第一段", "第二段"],
        dimensions=2,
        encoding_format="float",
    )
    assert result.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert result.input_tokens == 7


def test_openai_compatible_provider_maps_sdk_failure_to_safe_error() -> None:
    """第三方 SDK 异常应转换为稳定错误码，不向上泄露密钥或响应正文。"""
    create = AsyncMock(side_effect=RuntimeError("secret upstream detail"))
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    provider = OpenAICompatibleEmbeddingProvider(
        profile=build_profile(provider="openai_compatible", dimension=2),
        client=client,
    )

    with pytest.raises(EmbeddingError) as error:
        asyncio.run(provider.embed(EmbeddingRequest(texts=("测试",))))

    assert error.value.code == EmbeddingErrorCode.PROVIDER_REQUEST_FAILED
    assert "secret upstream detail" not in error.value.message


def test_embedding_factory_defaults_to_mock_profile() -> None:
    """未配置真实密钥时，工厂应创建不会消耗额度的 Mock Provider。"""
    factory = EmbeddingProviderFactory(EmbeddingSettings())

    profile = factory.default_profile()
    provider = factory.create_provider(profile)

    assert profile == EmbeddingProfile(
        provider="mock",
        model="mock-hash-1024",
        dimension=1024,
    )
    assert isinstance(provider, MockEmbeddingProvider)


def test_embedding_settings_rejects_non_database_dimension() -> None:
    """第一版数据库列固定为 vector(1024)，运行配置不能悄悄改成其他维度。"""
    with pytest.raises(ValueError):
        EmbeddingSettings(dimension=768)


def build_profile(
    provider: str = "mock",
    dimension: int = 2,
) -> EmbeddingProfile:
    """构造测试使用的最小 Embedding 配置快照。"""
    return EmbeddingProfile(
        provider=provider,
        model="test-model",
        dimension=dimension,
    )


def vector_norm(vector: tuple[float, ...]) -> float:
    """计算测试向量的 L2 范数。"""
    return math.sqrt(sum(value * value for value in vector))


def cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    """对已归一化 Mock 向量计算余弦相似度。"""
    return sum(left_value * right_value for left_value, right_value in zip(left, right))
