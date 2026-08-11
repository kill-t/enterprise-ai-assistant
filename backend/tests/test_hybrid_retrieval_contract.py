import asyncio
from uuid import uuid4

import pytest

from app.modules.knowledge.retrieval.lexical_retriever import (
    LexicalRetrievalCandidate,
    LexicalRetrievalError,
    LexicalRetrievalRequest,
    LexicalRetrievalResult,
)
from app.modules.knowledge.retrieval.mock_lexical_retriever import (
    MockLexicalRetriever,
)
from app.modules.knowledge.retrieval.reciprocal_rank_fusion import (
    RankedRetrievalList,
    ReciprocalRankFusion,
    RetrievalChannel,
)


def test_lexical_request_normalizes_scope_and_validates_budget() -> None:
    """词法检索必须携带用户范围、去重后的知识库范围和有界候选数量。"""
    user_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()

    request = LexicalRetrievalRequest(
        user_id=user_id,
        knowledge_base_ids=(first_id, first_id, second_id),
        query="  蓝鲸验收时间  ",
        candidate_limit=12,
    )

    assert request.query == "蓝鲸验收时间"
    assert request.knowledge_base_ids == (first_id, second_id)

    with pytest.raises(ValueError, match="知识库"):
        LexicalRetrievalRequest(
            user_id=user_id,
            knowledge_base_ids=(),
            query="有效问题",
            candidate_limit=10,
        )
    with pytest.raises(ValueError, match="candidate_limit"):
        LexicalRetrievalRequest(
            user_id=user_id,
            knowledge_base_ids=(first_id,),
            query="有效问题",
            candidate_limit=0,
        )


def test_lexical_result_cannot_escape_scope_or_candidate_budget() -> None:
    """词法实现不能返回其他知识库的 Chunk，也不能超过第一阶段预算。"""
    user_id = uuid4()
    allowed_id = uuid4()
    request = LexicalRetrievalRequest(
        user_id=user_id,
        knowledge_base_ids=(allowed_id,),
        query="项目负责人",
        candidate_limit=1,
    )
    allowed = build_lexical_candidate(
        knowledge_base_id=allowed_id,
        lexical_score=2.1,
        chunk_index=0,
    )
    foreign = build_lexical_candidate(
        knowledge_base_id=uuid4(),
        lexical_score=3.0,
        chunk_index=1,
    )

    LexicalRetrievalResult(candidates=(allowed,)).ensure_valid_for(request)
    with pytest.raises(LexicalRetrievalError, match="知识库范围之外"):
        LexicalRetrievalResult(candidates=(foreign,)).ensure_valid_for(request)
    with pytest.raises(LexicalRetrievalError, match="候选预算"):
        LexicalRetrievalResult(candidates=(foreign, allowed)).ensure_valid_for(
            request
        )

    lower = build_lexical_candidate(
        knowledge_base_id=allowed_id,
        lexical_score=1.0,
        chunk_index=2,
    )
    with pytest.raises(ValueError, match="降序"):
        LexicalRetrievalResult(candidates=(lower, allowed))


def test_mock_lexical_retriever_records_request_and_supports_failure() -> None:
    """Mock 实现应返回有界结果，并能稳定模拟后端不可用。"""
    knowledge_base_id = uuid4()
    candidates = tuple(
        build_lexical_candidate(
            knowledge_base_id=knowledge_base_id,
            lexical_score=3.0 - index,
            chunk_index=index,
        )
        for index in range(3)
    )
    request = LexicalRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(knowledge_base_id,),
        query="MinIO",
        candidate_limit=2,
    )
    retriever = MockLexicalRetriever(candidates=candidates)

    result = asyncio.run(retriever.retrieve(request))

    assert retriever.requests == [request]
    assert result.candidates == candidates[:2]
    result.ensure_valid_for(request)

    with pytest.raises(LexicalRetrievalError, match="模拟 BM25 超时"):
        asyncio.run(
            MockLexicalRetriever(failure_message="模拟 BM25 超时").retrieve(
                request
            )
        )


def test_rrf_rewards_chunks_ranked_by_both_channels() -> None:
    """同时进入向量榜和词法榜前列的 Chunk 应获得两路贡献并排到前面。"""
    first = uuid4()
    second = uuid4()
    vector_only = uuid4()
    lexical_only = uuid4()
    rankings = (
        RankedRetrievalList(
            channel=RetrievalChannel.VECTOR,
            chunk_ids=(first, second, vector_only),
        ),
        RankedRetrievalList(
            channel=RetrievalChannel.LEXICAL,
            chunk_ids=(second, lexical_only, first),
        ),
    )

    fused = ReciprocalRankFusion(rank_constant=60).fuse(rankings, limit=4)

    assert [item.chunk_id for item in fused] == [
        second,
        first,
        lexical_only,
        vector_only,
    ]
    assert [(item.channel, item.rank) for item in fused[0].contributions] == [
        (RetrievalChannel.VECTOR, 2),
        (RetrievalChannel.LEXICAL, 1),
    ]
    assert fused[0].fused_score == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_uses_rank_not_incomparable_raw_scores_and_has_stable_ties() -> None:
    """融合输入只包含名次；同分候选按首次出现顺序稳定返回。"""
    vector_first = uuid4()
    lexical_first = uuid4()
    rankings = (
        RankedRetrievalList(
            channel=RetrievalChannel.VECTOR,
            chunk_ids=(vector_first,),
        ),
        RankedRetrievalList(
            channel=RetrievalChannel.LEXICAL,
            chunk_ids=(lexical_first,),
        ),
    )

    fused = ReciprocalRankFusion().fuse(rankings, limit=2)

    assert [item.chunk_id for item in fused] == [vector_first, lexical_first]
    assert fused[0].fused_score == fused[1].fused_score


def test_rrf_rejects_duplicate_channel_or_duplicate_chunk_rank() -> None:
    """同一榜单的重复 Chunk 和同一渠道的重复榜单都会破坏名次含义。"""
    chunk_id = uuid4()
    with pytest.raises(ValueError, match="重复 Chunk"):
        RankedRetrievalList(
            channel=RetrievalChannel.VECTOR,
            chunk_ids=(chunk_id, chunk_id),
        )

    duplicate_channels = (
        RankedRetrievalList(
            channel=RetrievalChannel.VECTOR,
            chunk_ids=(uuid4(),),
        ),
        RankedRetrievalList(
            channel=RetrievalChannel.VECTOR,
            chunk_ids=(uuid4(),),
        ),
    )
    with pytest.raises(ValueError, match="重复检索渠道"):
        ReciprocalRankFusion().fuse(duplicate_channels, limit=2)


def build_lexical_candidate(
    *,
    knowledge_base_id,
    lexical_score: float,
    chunk_index: int,
) -> LexicalRetrievalCandidate:
    """构造包含完整来源信息的词法候选，避免测试依赖数据库。"""
    return LexicalRetrievalCandidate(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name="蓝鲸项目",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project.md",
        chunk_index=chunk_index,
        content=f"第 {chunk_index} 个项目资料片段",
        content_hash=f"{chunk_index:064x}",
        heading_path="项目资料",
        page_start=None,
        page_end=None,
        token_count=12,
        lexical_score=lexical_score,
    )
