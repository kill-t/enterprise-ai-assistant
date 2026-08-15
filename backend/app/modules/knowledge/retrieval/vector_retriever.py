"""定义混合检索所需的向量候选接口和安全边界。"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class VectorRetrievalRequest(Protocol):
    """描述向量通道从混合请求中实际读取的最小字段集合。"""

    user_id: UUID
    knowledge_base_ids: tuple[UUID, ...]
    query: str
    channel_candidate_limit: int


class VectorRetrievalError(RuntimeError):
    """表示向量候选通道不可用或返回了违反契约的结果。"""


class VectorRetrievalScopeError(VectorRetrievalError):
    """表示知识库缺失、越权或已归档，禁止被降级逻辑吞掉。"""


@dataclass(frozen=True, slots=True)
class VectorRetrievalCandidate:
    """保存向量渠道召回的完整来源和渠道内余弦相似度。"""

    knowledge_base_id: UUID
    knowledge_base_name: str
    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    vector_score: float

    def __post_init__(self) -> None:
        """阻止无法排序或超出余弦相似度范围的分数进入融合层。"""
        if not math.isfinite(self.vector_score) or not -1.0 <= self.vector_score <= 1.0:
            raise ValueError("向量候选分数必须是 -1 到 1 之间的有限数")


@dataclass(frozen=True, slots=True)
class VectorRetrievalResult:
    """封装一个已按向量相似度降序排列的候选榜。"""

    candidates: tuple[VectorRetrievalCandidate, ...]

    def __post_init__(self) -> None:
        """保证每个 Chunk 只出现一次，并让 RRF 收到稳定的渠道名次。"""
        chunk_ids = [candidate.chunk_id for candidate in self.candidates]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("向量检索结果不能包含重复 Chunk")
        scores = [candidate.vector_score for candidate in self.candidates]
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ValueError("向量检索结果必须按 vector_score 降序排列")

    def ensure_valid_for(self, request: VectorRetrievalRequest) -> None:
        """校验候选没有突破请求的数量和知识库权限范围。"""
        if len(self.candidates) > request.channel_candidate_limit:
            raise VectorRetrievalError("向量检索结果超过候选预算")
        allowed_ids = set(request.knowledge_base_ids)
        if any(
            candidate.knowledge_base_id not in allowed_ids
            for candidate in self.candidates
        ):
            raise VectorRetrievalError("向量检索结果包含知识库范围之外的 Chunk")


class BaseVectorRetriever(ABC):
    """统一真实 pgvector 和测试 Mock 的异步候选调用方式。"""

    @abstractmethod
    async def retrieve(
        self,
        request: VectorRetrievalRequest,
    ) -> VectorRetrievalResult:
        """在请求的用户和知识库范围内返回有序向量候选。"""
