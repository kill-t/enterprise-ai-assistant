"""提供无需模型额度、结果完全可控的证据选择测试实现。"""

from uuid import UUID

from app.modules.knowledge.retrieval.evidence_selection import (
    BaseEvidenceSelector,
    CandidateT,
    EvidenceSelectionError,
    EvidenceSelectionReason,
    EvidenceSelectionRequest,
    EvidenceSelectionResult,
)


class MockEvidenceSelector(BaseEvidenceSelector[CandidateT]):
    """按预设 Chunk 顺序选择、强制拒答或模拟失败。"""

    def __init__(
        self,
        *,
        selected_chunk_ids: tuple[UUID, ...] | None = None,
        force_reject: bool = False,
        failure_message: str | None = None,
    ) -> None:
        """保存可重复的测试行为，并暴露请求记录供断言。"""
        if selected_chunk_ids is not None and len(selected_chunk_ids) != len(
            set(selected_chunk_ids)
        ):
            raise ValueError("Mock Selector 不能配置重复 Chunk")
        self.selected_chunk_ids = selected_chunk_ids
        self.force_reject = force_reject
        self.failure_message = failure_message
        self.requests: list[EvidenceSelectionRequest[CandidateT]] = []

    async def select(
        self,
        request: EvidenceSelectionRequest[CandidateT],
    ) -> EvidenceSelectionResult[CandidateT]:
        """记录调用并执行预设分支，不依赖外部模型或网络。"""
        self.requests.append(request)
        if self.failure_message is not None:
            raise EvidenceSelectionError(self.failure_message)
        if self.force_reject:
            return EvidenceSelectionResult(
                answerable=False,
                selected_candidates=(),
                reason=EvidenceSelectionReason.SELECTOR_REJECTED,
            )

        if self.selected_chunk_ids is None:
            selected = request.candidates[: request.top_k]
        else:
            candidate_by_id = {
                candidate.chunk_id: candidate for candidate in request.candidates
            }
            missing_ids = [
                chunk_id
                for chunk_id in self.selected_chunk_ids
                if chunk_id not in candidate_by_id
            ]
            if missing_ids:
                raise EvidenceSelectionError("Mock Selector 配置了不存在的 Chunk")
            selected = tuple(
                candidate_by_id[chunk_id]
                for chunk_id in self.selected_chunk_ids[: request.top_k]
            )

        if not selected:
            return EvidenceSelectionResult(
                answerable=False,
                selected_candidates=(),
                reason=EvidenceSelectionReason.SELECTOR_REJECTED,
            )
        return EvidenceSelectionResult(
            answerable=True,
            selected_candidates=selected,
            reason=EvidenceSelectionReason.SELECTED,
        )
