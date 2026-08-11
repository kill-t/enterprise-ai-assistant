import asyncio
from uuid import uuid4

import pytest

from app.modules.knowledge.retrieval.evidence_selection import (
    EvidenceSelectionError,
    EvidenceSelectionReason,
    EvidenceSelectionRequest,
    EvidenceSelectionResult,
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.retrieval.mock_evidence_selector import (
    MockEvidenceSelector,
)
from app.modules.knowledge.search_models import KnowledgeSearchCandidate


def test_threshold_selector_filters_sorts_and_limits_candidates() -> None:
    """默认实现应完整复现旧的分数阈值、降序排序和 Top-K 行为。"""
    candidates = (
        build_candidate(score=0.61, chunk_index=1),
        build_candidate(score=0.92, chunk_index=0),
        build_candidate(score=0.49, chunk_index=2),
    )
    request = EvidenceSelectionRequest(
        query="项目会议如何安排？",
        candidates=candidates,
        top_k=2,
        similarity_threshold=0.5,
    )

    result = asyncio.run(ThresholdEvidenceSelector().select(request))

    assert result.answerable is True
    assert result.reason == EvidenceSelectionReason.SELECTED
    assert [candidate.score for candidate in result.selected_candidates] == [
        0.92,
        0.61,
    ]


@pytest.mark.parametrize(
    ("scores", "expected_reason"),
    [
        ((), EvidenceSelectionReason.NO_CANDIDATES),
        ((0.49,), EvidenceSelectionReason.BELOW_THRESHOLD),
    ],
)
def test_threshold_selector_returns_explicit_insufficient_reason(
    scores: tuple[float, ...],
    expected_reason: EvidenceSelectionReason,
) -> None:
    """没有候选和候选分数不足是两种可观测原因，但都必须安全拒答。"""
    candidates = tuple(
        build_candidate(score=score, chunk_index=index)
        for index, score in enumerate(scores)
    )
    result = asyncio.run(
        ThresholdEvidenceSelector().select(
            EvidenceSelectionRequest(
                query="资料中有答案吗？",
                candidates=candidates,
                top_k=3,
                similarity_threshold=0.5,
            )
        )
    )

    assert result.answerable is False
    assert result.selected_candidates == ()
    assert result.reason == expected_reason


def test_mock_selector_can_rerank_or_reject_the_same_candidates() -> None:
    """Mock 应可控制第二阶段决策，供 Service 集成测试稳定复现各种分支。"""
    first = build_candidate(score=0.9, chunk_index=0)
    second = build_candidate(score=0.8, chunk_index=1)
    request = EvidenceSelectionRequest(
        query="哪个证据更充分？",
        candidates=(first, second),
        top_k=2,
        similarity_threshold=0.5,
    )
    reranker = MockEvidenceSelector(
        selected_chunk_ids=(second.chunk_id, first.chunk_id)
    )

    reranked = asyncio.run(reranker.select(request))
    rejected = asyncio.run(MockEvidenceSelector(force_reject=True).select(request))

    assert reranker.requests == [request]
    assert [item.chunk_id for item in reranked.selected_candidates] == [
        second.chunk_id,
        first.chunk_id,
    ]
    assert rejected.answerable is False
    assert rejected.reason == EvidenceSelectionReason.SELECTOR_REJECTED


def test_mock_selector_failure_is_not_disguised_as_no_evidence() -> None:
    """第二阶段故障必须抛出独立异常，不能返回空列表伪装成正常拒答。"""
    selector = MockEvidenceSelector(failure_message="模拟重排序服务超时")
    request = EvidenceSelectionRequest(
        query="项目负责人是谁？",
        candidates=(build_candidate(score=0.9, chunk_index=0),),
        top_k=1,
        similarity_threshold=0.5,
    )

    with pytest.raises(EvidenceSelectionError, match="模拟重排序服务超时"):
        asyncio.run(selector.select(request))


def test_selection_result_cannot_fabricate_candidate_outside_first_stage() -> None:
    """第二阶段只能重排和删减已召回候选，不能凭空构造未授权 Chunk。"""
    recalled = build_candidate(score=0.8, chunk_index=0)
    fabricated = build_candidate(score=0.9, chunk_index=1)
    request = EvidenceSelectionRequest(
        query="项目资料",
        candidates=(recalled,),
        top_k=1,
        similarity_threshold=0.5,
    )
    result = EvidenceSelectionResult(
        answerable=True,
        selected_candidates=(fabricated,),
        reason=EvidenceSelectionReason.SELECTED,
    )

    with pytest.raises(EvidenceSelectionError, match="候选集合之外"):
        result.ensure_valid_for(request)


def test_selection_request_rejects_invalid_budget() -> None:
    """内部调用也必须限制 Top-K 与阈值，不能只依赖 HTTP Schema。"""
    with pytest.raises(ValueError, match="top_k"):
        EvidenceSelectionRequest(
            query="有效问题",
            candidates=(),
            top_k=0,
            similarity_threshold=0.5,
        )
    with pytest.raises(ValueError, match="相似度阈值"):
        EvidenceSelectionRequest(
            query="有效问题",
            candidates=(),
            top_k=1,
            similarity_threshold=1.1,
        )


def build_candidate(score: float, chunk_index: int) -> KnowledgeSearchCandidate:
    """构造带稳定检索字段的候选 Chunk。"""
    return KnowledgeSearchCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project.md",
        chunk_index=chunk_index,
        content=f"第 {chunk_index} 个候选片段",
        heading_path="项目资料",
        page_start=None,
        page_end=None,
        token_count=10,
        score=score,
    )
