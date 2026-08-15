import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.modules.knowledge.retrieval.hybrid_retriever import (
    HybridRetrievalError,
    HybridRetrievalRequest,
    HybridRetriever,
)
from app.modules.knowledge.retrieval.knowledge_search_vector_retriever import (
    KnowledgeSearchVectorRetriever,
)
from app.modules.knowledge.retrieval.lexical_retriever import (
    LexicalRetrievalCandidate,
)
from app.modules.knowledge.retrieval.mock_lexical_retriever import (
    MockLexicalRetriever,
)
from app.modules.knowledge.retrieval.mock_vector_retriever import (
    MockVectorRetriever,
)
from app.modules.knowledge.retrieval.reciprocal_rank_fusion import (
    RetrievalChannel,
)
from app.modules.knowledge.retrieval.vector_retriever import (
    VectorRetrievalCandidate,
    VectorRetrievalError,
    VectorRetrievalScopeError,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchHit,
    MultiKnowledgeSearchResponse,
)


def test_hybrid_request_normalizes_scope_and_validates_two_budgets() -> None:
    """混合请求应去重知识库，并分别约束渠道候选和最终融合数量。"""
    first_id = uuid4()
    second_id = uuid4()

    request = HybridRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(first_id, first_id, second_id),
        query="  蓝鲸项目负责人  ",
        channel_candidate_limit=12,
        result_limit=3,
    )

    assert request.query == "蓝鲸项目负责人"
    assert request.knowledge_base_ids == (first_id, second_id)

    with pytest.raises(ValueError, match="channel_candidate_limit"):
        HybridRetrievalRequest(
            user_id=uuid4(),
            knowledge_base_ids=(first_id,),
            query="有效问题",
            channel_candidate_limit=21,
            result_limit=3,
        )
    with pytest.raises(ValueError, match="result_limit"):
        HybridRetrievalRequest(
            user_id=uuid4(),
            knowledge_base_ids=(first_id,),
            query="有效问题",
            channel_candidate_limit=10,
            result_limit=0,
        )


def test_hybrid_retriever_fuses_rankings_and_preserves_channel_scores() -> None:
    """共同命中的 Chunk 应排在前面，同时保留两路原始分数和可解释贡献。"""
    knowledge_base_id = uuid4()
    vector_first = build_vector_candidate(
        knowledge_base_id=knowledge_base_id,
        vector_score=0.91,
        chunk_index=0,
    )
    shared_vector = build_vector_candidate(
        knowledge_base_id=knowledge_base_id,
        vector_score=0.82,
        chunk_index=1,
    )
    shared_lexical = build_lexical_candidate_from_vector(
        shared_vector,
        lexical_score=0.73,
    )
    lexical_only = build_lexical_candidate(
        knowledge_base_id=knowledge_base_id,
        lexical_score=0.66,
        chunk_index=2,
    )
    vector_retriever = MockVectorRetriever(
        candidates=(vector_first, shared_vector)
    )
    lexical_retriever = MockLexicalRetriever(
        candidates=(shared_lexical, lexical_only)
    )
    request = build_request(knowledge_base_id=knowledge_base_id)

    result = asyncio.run(
        HybridRetriever(
            vector_retriever=vector_retriever,
            lexical_retriever=lexical_retriever,
        ).retrieve(request)
    )

    assert [candidate.chunk_id for candidate in result.candidates] == [
        shared_vector.chunk_id,
        vector_first.chunk_id,
        lexical_only.chunk_id,
    ]
    shared = result.candidates[0]
    assert shared.vector_score == pytest.approx(0.82)
    assert shared.lexical_score == pytest.approx(0.73)
    assert [item.channel for item in shared.contributions] == [
        RetrievalChannel.VECTOR,
        RetrievalChannel.LEXICAL,
    ]
    assert result.successful_channels == (
        RetrievalChannel.VECTOR,
        RetrievalChannel.LEXICAL,
    )
    assert result.degraded_channels == ()
    assert vector_retriever.requests == [request]
    lexical_request = lexical_retriever.requests[0]
    assert lexical_request.user_id == request.user_id
    assert lexical_request.knowledge_base_ids == request.knowledge_base_ids
    assert lexical_request.candidate_limit == request.channel_candidate_limit


def test_hybrid_retriever_degrades_to_lexical_when_vector_is_unavailable() -> None:
    """Embedding 服务不可用时应明确记录降级，并保留成功的词法候选。"""
    knowledge_base_id = uuid4()
    lexical = build_lexical_candidate(
        knowledge_base_id=knowledge_base_id,
        lexical_score=0.8,
        chunk_index=0,
    )
    result = asyncio.run(
        HybridRetriever(
            vector_retriever=MockVectorRetriever(
                failure_message="模拟 Embedding 超时"
            ),
            lexical_retriever=MockLexicalRetriever(candidates=(lexical,)),
        ).retrieve(build_request(knowledge_base_id=knowledge_base_id))
    )

    assert [candidate.chunk_id for candidate in result.candidates] == [
        lexical.chunk_id
    ]
    assert result.candidates[0].vector_score is None
    assert result.degraded_channels == (RetrievalChannel.VECTOR,)


def test_hybrid_retriever_degrades_to_vector_when_lexical_is_unavailable() -> None:
    """pg_trgm 故障不能清空已经成功获得的向量候选。"""
    knowledge_base_id = uuid4()
    vector = build_vector_candidate(
        knowledge_base_id=knowledge_base_id,
        vector_score=0.88,
        chunk_index=0,
    )
    result = asyncio.run(
        HybridRetriever(
            vector_retriever=MockVectorRetriever(candidates=(vector,)),
            lexical_retriever=MockLexicalRetriever(
                failure_message="模拟 pg_trgm 不可用"
            ),
        ).retrieve(build_request(knowledge_base_id=knowledge_base_id))
    )

    assert [candidate.chunk_id for candidate in result.candidates] == [
        vector.chunk_id
    ]
    assert result.candidates[0].lexical_score is None
    assert result.degraded_channels == (RetrievalChannel.LEXICAL,)


def test_hybrid_retriever_fails_when_both_channels_are_unavailable() -> None:
    """两路都失败属于系统故障，不能伪装成“知识库没有答案”。"""
    service = HybridRetriever(
        vector_retriever=MockVectorRetriever(failure_message="向量服务超时"),
        lexical_retriever=MockLexicalRetriever(failure_message="关键词数据库故障"),
    )

    with pytest.raises(HybridRetrievalError, match="混合检索服务暂时不可用"):
        asyncio.run(service.retrieve(build_request(knowledge_base_id=uuid4())))


def test_two_successful_empty_channels_are_normal_no_evidence() -> None:
    """成功返回空榜表示没有候选，与两个渠道运行失败是不同状态。"""
    result = asyncio.run(
        HybridRetriever(
            vector_retriever=MockVectorRetriever(),
            lexical_retriever=MockLexicalRetriever(),
        ).retrieve(build_request(knowledge_base_id=uuid4()))
    )

    assert result.candidates == ()
    assert result.successful_channels == (
        RetrievalChannel.VECTOR,
        RetrievalChannel.LEXICAL,
    )
    assert result.degraded_channels == ()


def test_vector_scope_failure_never_degrades_into_an_empty_security_result() -> None:
    """知识库缺失或越权必须交给上层处理，不能通过词法通道降级掩盖。"""
    lexical_retriever = MockLexicalRetriever()
    service = HybridRetriever(
        vector_retriever=MockVectorRetriever(scope_failure=True),
        lexical_retriever=lexical_retriever,
    )

    with pytest.raises(VectorRetrievalScopeError, match="知识库范围无效"):
        asyncio.run(service.retrieve(build_request(knowledge_base_id=uuid4())))

    assert lexical_retriever.requests == []


def test_hybrid_retriever_rejects_conflicting_provenance_for_same_chunk() -> None:
    """两路若把同一 Chunk 指向不同正文，必须停止而不是生成不可审计引用。"""
    knowledge_base_id = uuid4()
    vector = build_vector_candidate(
        knowledge_base_id=knowledge_base_id,
        vector_score=0.9,
        chunk_index=0,
    )
    lexical = build_lexical_candidate_from_vector(vector, lexical_score=0.8)
    conflicting = replace(lexical, content="被错误替换的正文")

    with pytest.raises(HybridRetrievalError, match="来源信息不一致"):
        asyncio.run(
            HybridRetriever(
                vector_retriever=MockVectorRetriever(candidates=(vector,)),
                lexical_retriever=MockLexicalRetriever(candidates=(conflicting,)),
            ).retrieve(build_request(knowledge_base_id=knowledge_base_id))
        )


def test_vector_adapter_loads_owned_scope_and_maps_existing_search_results() -> None:
    """真实向量 Adapter 应复用现有 SearchService，不复制 Embedding 和权限逻辑。"""
    user_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    first = SimpleNamespace(id=first_id, user_id=user_id, is_archived=False)
    second = SimpleNamespace(id=second_id, user_id=user_id, is_archived=False)
    hit = build_multi_hit(knowledge_base_id=second_id)
    search_service = SimpleNamespace(
        search_many=AsyncMock(
            return_value=MultiKnowledgeSearchResponse(
                query="项目负责人",
                searched_knowledge_base_ids=[first_id, second_id],
                similarity_threshold=-1.0,
                results=[hit],
            )
        )
    )
    repository = SimpleNamespace(
        list_by_ids_for_user=AsyncMock(return_value=[second, first])
    )
    retriever = KnowledgeSearchVectorRetriever(
        knowledge_base_repository=repository,
        search_service=search_service,
    )
    request = HybridRetrievalRequest(
        user_id=user_id,
        knowledge_base_ids=(first_id, second_id),
        query="项目负责人",
        channel_candidate_limit=8,
        result_limit=3,
    )

    result = asyncio.run(retriever.retrieve(request))

    repository.list_by_ids_for_user.assert_awaited_once_with(
        user_id=user_id,
        knowledge_base_ids=[first_id, second_id],
    )
    search_service.search_many.assert_awaited_once()
    call = search_service.search_many.await_args.kwargs
    assert call["knowledge_bases"] == [first, second]
    assert call["request"].query == "项目负责人"
    assert call["request"].top_k == 8
    assert result.candidates[0].chunk_id == hit.chunk_id
    assert result.candidates[0].vector_score == pytest.approx(hit.score)


def test_vector_adapter_rejects_missing_scope_before_embedding() -> None:
    """批量所有权查询少返回一个知识库时，不得继续调用向量服务。"""
    existing = SimpleNamespace(id=uuid4())
    search_service = SimpleNamespace(search_many=AsyncMock())
    retriever = KnowledgeSearchVectorRetriever(
        knowledge_base_repository=SimpleNamespace(
            list_by_ids_for_user=AsyncMock(return_value=[existing])
        ),
        search_service=search_service,
    )
    request = HybridRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(existing.id, uuid4()),
        query="越权资料",
        channel_candidate_limit=8,
        result_limit=3,
    )

    with pytest.raises(VectorRetrievalScopeError, match="知识库范围无效"):
        asyncio.run(retriever.retrieve(request))

    search_service.search_many.assert_not_awaited()


def test_vector_adapter_hides_provider_failure_details() -> None:
    """现有 SearchService 的第三方故障应成为安全通道错误，供 Hybrid 决定降级。"""
    user_id = uuid4()
    knowledge_base_id = uuid4()
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        user_id=user_id,
        is_archived=False,
    )
    internal_message = "https://embedding.internal.example 包含敏感响应"
    retriever = KnowledgeSearchVectorRetriever(
        knowledge_base_repository=SimpleNamespace(
            list_by_ids_for_user=AsyncMock(return_value=[knowledge_base])
        ),
        search_service=SimpleNamespace(
            search_many=AsyncMock(
                side_effect=BusinessException(
                    code=ErrorCode.KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE,
                    message=internal_message,
                    status_code=503,
                )
            )
        ),
    )

    with pytest.raises(VectorRetrievalError) as error:
        asyncio.run(
            retriever.retrieve(
                build_request(knowledge_base_id=knowledge_base_id)
            )
        )

    assert "暂时不可用" in str(error.value)
    assert internal_message not in str(error.value)


def build_request(*, knowledge_base_id: UUID) -> HybridRetrievalRequest:
    """构造一个两路各取 5 条、最终保留 3 条的标准混合请求。"""
    return HybridRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(knowledge_base_id,),
        query="项目负责人",
        channel_candidate_limit=5,
        result_limit=3,
    )


def build_vector_candidate(
    *,
    knowledge_base_id: UUID,
    vector_score: float,
    chunk_index: int,
) -> VectorRetrievalCandidate:
    """构造带完整引用字段的向量候选。"""
    return VectorRetrievalCandidate(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name="蓝鲸项目",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project.md",
        chunk_index=chunk_index,
        content=f"第 {chunk_index} 个项目资料片段",
        heading_path="项目资料",
        page_start=None,
        page_end=None,
        token_count=12,
        vector_score=vector_score,
    )


def build_lexical_candidate(
    *,
    knowledge_base_id: UUID,
    lexical_score: float,
    chunk_index: int,
) -> LexicalRetrievalCandidate:
    """构造只被词法渠道命中的候选。"""
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


def build_lexical_candidate_from_vector(
    vector: VectorRetrievalCandidate,
    *,
    lexical_score: float,
) -> LexicalRetrievalCandidate:
    """让两路候选指向同一个真实 Chunk，用于验证融合和来源一致性。"""
    return LexicalRetrievalCandidate(
        knowledge_base_id=vector.knowledge_base_id,
        knowledge_base_name=vector.knowledge_base_name,
        chunk_id=vector.chunk_id,
        document_id=vector.document_id,
        filename=vector.filename,
        chunk_index=vector.chunk_index,
        content=vector.content,
        content_hash="a" * 64,
        heading_path=vector.heading_path,
        page_start=vector.page_start,
        page_end=vector.page_end,
        token_count=vector.token_count,
        lexical_score=lexical_score,
    )


def build_multi_hit(*, knowledge_base_id: UUID) -> MultiKnowledgeSearchHit:
    """构造现有 SearchService 的多知识库响应，用于验证真实 Adapter 映射。"""
    return MultiKnowledgeSearchHit(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name="蓝鲸项目",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project.md",
        chunk_index=0,
        content="蓝鲸项目负责人是林晓。",
        heading_path="项目身份",
        page_start=None,
        page_end=None,
        token_count=15,
        score=0.91,
    )
