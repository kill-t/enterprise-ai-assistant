"""定义可替换的证据重排与答案充分性判断契约。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar
from uuid import UUID


class EvidenceCandidate(Protocol):
    """约束第二阶段所需的最小候选字段，避免绑定具体 Repository 类型。"""

    chunk_id: UUID
    score: float


CandidateT = TypeVar("CandidateT", bound=EvidenceCandidate)


class EvidenceSelectionReason(str, Enum):
    """记录第二阶段为什么选择证据或拒绝回答。"""

    SELECTED = "selected"
    NO_CANDIDATES = "no_candidates"
    BELOW_THRESHOLD = "below_threshold"
    SELECTOR_REJECTED = "selector_rejected"


class EvidenceSelectionError(RuntimeError):
    """表示 Selector 自身失败，与正常的“证据不足”严格区分。"""


@dataclass(frozen=True, slots=True)
class EvidenceSelectionRequest(Generic[CandidateT]):
    """把第一阶段候选和本次选择预算传入第二阶段。"""

    query: str
    candidates: tuple[CandidateT, ...]
    top_k: int
    similarity_threshold: float

    def __post_init__(self) -> None:
        """保护内部调用边界，避免绕过 HTTP 校验传入非法预算。"""
        if not self.query.strip():
            raise ValueError("证据选择 query 不能为空")
        if not 1 <= self.top_k <= 20:
            raise ValueError("证据选择 top_k 必须在 1 到 20 之间")
        if not -1.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("证据选择相似度阈值必须在 -1 到 1 之间")


@dataclass(frozen=True, slots=True)
class EvidenceSelectionResult(Generic[CandidateT]):
    """返回最终有序证据，以及能否进入回答层的明确决定。"""

    answerable: bool
    selected_candidates: tuple[CandidateT, ...]
    reason: EvidenceSelectionReason

    def __post_init__(self) -> None:
        """要求答案决定与候选集合一致，防止下游误用矛盾状态。"""
        if self.answerable != bool(self.selected_candidates):
            raise ValueError("证据选择结果的 answerable 与候选集合不一致")
        chunk_ids = [candidate.chunk_id for candidate in self.selected_candidates]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("证据选择结果不能包含重复 Chunk")

    def ensure_valid_for(
        self,
        request: EvidenceSelectionRequest[CandidateT],
    ) -> None:
        """阻止 Selector 超预算或返回第一阶段没有召回的伪造候选。"""
        if len(self.selected_candidates) > request.top_k:
            raise EvidenceSelectionError("证据选择结果超过本次 Top-K 预算")
        candidate_ids = {candidate.chunk_id for candidate in request.candidates}
        if any(
            candidate.chunk_id not in candidate_ids
            for candidate in self.selected_candidates
        ):
            raise EvidenceSelectionError("证据选择结果包含候选集合之外的 Chunk")


class BaseEvidenceSelector(ABC, Generic[CandidateT]):
    """统一重排、过滤和答案充分性判断的异步调用方式。"""

    @abstractmethod
    async def select(
        self,
        request: EvidenceSelectionRequest[CandidateT],
    ) -> EvidenceSelectionResult[CandidateT]:
        """从有序或无序候选中选择足以支持回答的最终证据。"""


class ThresholdEvidenceSelector(BaseEvidenceSelector[CandidateT]):
    """按向量阈值选择证据，用于保持第一版线上行为不变。"""

    async def select(
        self,
        request: EvidenceSelectionRequest[CandidateT],
    ) -> EvidenceSelectionResult[CandidateT]:
        """稳定按分数降序排列，再应用阈值与 Top-K。"""
        if not request.candidates:
            return EvidenceSelectionResult(
                answerable=False,
                selected_candidates=(),
                reason=EvidenceSelectionReason.NO_CANDIDATES,
            )

        # sorted 是稳定排序；同分候选继续保持 Repository 的原始顺序。
        selected = tuple(
            candidate
            for candidate in sorted(
                request.candidates,
                key=lambda candidate: -candidate.score,
            )
            if candidate.score >= request.similarity_threshold
        )[: request.top_k]
        if not selected:
            return EvidenceSelectionResult(
                answerable=False,
                selected_candidates=(),
                reason=EvidenceSelectionReason.BELOW_THRESHOLD,
            )
        return EvidenceSelectionResult(
            answerable=True,
            selected_candidates=selected,
            reason=EvidenceSelectionReason.SELECTED,
        )
