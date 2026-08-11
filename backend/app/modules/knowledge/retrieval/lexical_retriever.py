"""定义关键词候选检索的权限范围、结果结构和可替换接口。"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID


class LexicalRetrievalError(RuntimeError):
    """表示词法检索后端失败或返回了违反安全边界的结果。"""


@dataclass(frozen=True, slots=True)
class LexicalRetrievalRequest:
    """描述一次带用户所有权范围和候选预算的关键词检索。"""

    user_id: UUID
    knowledge_base_ids: tuple[UUID, ...]
    query: str
    candidate_limit: int

    def __post_init__(self) -> None:
        """清理问题并按首次出现顺序去重知识库，同时限制内部预算。"""
        normalized_query = self.query.strip()
        if not normalized_query:
            raise ValueError("词法检索 query 不能为空")
        if not self.knowledge_base_ids:
            raise ValueError("词法检索至少需要一个知识库")
        if not 1 <= self.candidate_limit <= 200:
            raise ValueError("词法检索 candidate_limit 必须在 1 到 200 之间")

        unique_ids = tuple(dict.fromkeys(self.knowledge_base_ids))
        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(self, "knowledge_base_ids", unique_ids)


@dataclass(frozen=True, slots=True)
class LexicalRetrievalCandidate:
    """保存词法渠道召回的完整 Chunk 来源和渠道内原始分数。"""

    knowledge_base_id: UUID
    knowledge_base_name: str
    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    content_hash: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    lexical_score: float

    def __post_init__(self) -> None:
        """约定词法分数越大越相关，并拒绝无法排序的非法数值。"""
        if not math.isfinite(self.lexical_score) or self.lexical_score < 0:
            raise ValueError("词法候选分数必须是大于等于零的有限数")


@dataclass(frozen=True, slots=True)
class LexicalRetrievalResult:
    """封装一个已经按词法相关性降序排列的候选列表。"""

    candidates: tuple[LexicalRetrievalCandidate, ...]

    def __post_init__(self) -> None:
        """保证 Chunk 唯一且分数降序，使 RRF 收到的名次具有稳定含义。"""
        chunk_ids = [candidate.chunk_id for candidate in self.candidates]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("词法检索结果不能包含重复 Chunk")
        scores = [candidate.lexical_score for candidate in self.candidates]
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ValueError("词法检索结果必须按 lexical_score 降序排列")

    def ensure_valid_for(self, request: LexicalRetrievalRequest) -> None:
        """阻止实现超预算或返回本次用户范围之外的知识库内容。"""
        if len(self.candidates) > request.candidate_limit:
            raise LexicalRetrievalError("词法检索结果超过候选预算")
        allowed_ids = set(request.knowledge_base_ids)
        if any(
            candidate.knowledge_base_id not in allowed_ids
            for candidate in self.candidates
        ):
            raise LexicalRetrievalError("词法检索结果包含知识库范围之外的 Chunk")


class BaseLexicalRetriever(ABC):
    """统一 pg_trgm、BM25 或其他关键词检索后端的异步调用方式。"""

    @abstractmethod
    async def retrieve(
        self,
        request: LexicalRetrievalRequest,
    ) -> LexicalRetrievalResult:
        """在请求指定的用户与知识库范围内返回有序关键词候选。"""
