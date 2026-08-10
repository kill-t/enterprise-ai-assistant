import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql

from app.common.enums import DocumentStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.infrastructure.embedding.schemas import EmbeddingProfile, EmbeddingResult
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.service.conversation_knowledge_search_service import (
    ConversationKnowledgeSearchService,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.knowledge_search_repository import (
    KnowledgeSearchRepository,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
    MultiKnowledgeSearchResponse,
)
from app.modules.knowledge.search_models import MultiKnowledgeSearchCandidate
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


def test_multi_repository_query_filters_all_knowledge_bases_in_one_vector_query() -> None:
    """跨库 SQL 应一次完成所有权、归档、READY 和模型过滤以及全局距离排序。"""
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)))
    repository = KnowledgeSearchRepository(db)
    user_id = uuid4()
    knowledge_base_ids = [uuid4(), uuid4()]

    results = asyncio.run(
        repository.search_candidates_across_knowledge_bases(
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            query_vector=(1.0, 0.0),
            embedding_model="mock-hash-1024",
            candidate_limit=12,
        )
    )

    statement = db.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert results == []
    assert "knowledge_bases.id IN" in sql
    assert "knowledge_bases.user_id" in sql
    assert "knowledge_bases.is_archived IS false" in sql
    assert "documents.status" in sql
    assert "document_chunks.embedding_model" in sql
    assert "document_chunks.content_hash" in sql
    assert "knowledge_bases.name" in sql
    assert "<=>" in sql
    assert "ORDER BY" in sql
    assert knowledge_base_ids in compiled.params.values()
    assert user_id in compiled.params.values()
    assert DocumentStatus.READY in compiled.params.values()
    assert 12 in compiled.params.values()


def test_multi_repository_detects_incompatible_vectors_for_any_selected_base() -> None:
    """一次检查必须找出任一知识库内与配置快照不一致的 READY Chunk。"""
    incompatible_id = uuid4()
    scalar_result = SimpleNamespace(all=lambda: [incompatible_id])
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = KnowledgeSearchRepository(db)
    user_id = uuid4()
    knowledge_base_ids = [incompatible_id, uuid4()]

    results = asyncio.run(
        repository.list_incompatible_ready_knowledge_base_ids(
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
        )
    )

    statement = db.scalars.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert results == [incompatible_id]
    assert "document_chunks.embedding_model != knowledge_bases.embedding_model" in sql
    assert "knowledge_bases.id IN" in sql
    assert "knowledge_bases.user_id" in sql
    assert "knowledge_bases.is_archived IS false" in sql
    assert knowledge_base_ids in compiled.params.values()


def test_core_search_embeds_once_then_deduplicates_and_globally_ranks() -> None:
    """同配置知识库应共用一个查询向量，并按全局分数去重后截取 Top-K。"""
    user_id = uuid4()
    first = build_knowledge_base(user_id=user_id, name="项目规范")
    second = build_knowledge_base(user_id=user_id, name="会议资料")
    embedding_service = SimpleNamespace(
        embed_texts=AsyncMock(
            return_value=EmbeddingResult(
                vectors=((1.0, 0.0),),
                model="mock-hash-1024",
                dimension=2,
                input_tokens=3,
            )
        )
    )
    embedding_factory = SimpleNamespace(
        create_service=Mock(return_value=embedding_service)
    )
    candidates = [
        build_candidate(
            knowledge_base=second,
            score=0.81,
            content_hash="b" * 64,
            chunk_index=1,
        ),
        build_candidate(
            knowledge_base=first,
            score=0.93,
            content_hash="a" * 64,
            chunk_index=0,
        ),
        # 与最高分片段正文相同，只能保留分数更高的第一条来源。
        build_candidate(
            knowledge_base=second,
            score=0.82,
            content_hash="a" * 64,
            chunk_index=2,
        ),
        build_candidate(
            knowledge_base=first,
            score=0.49,
            content_hash="c" * 64,
            chunk_index=3,
        ),
    ]
    search_repository = SimpleNamespace(
        list_incompatible_ready_knowledge_base_ids=AsyncMock(return_value=[]),
        search_candidates_across_knowledge_bases=AsyncMock(
            return_value=candidates
        ),
    )
    service = build_core_search_service(
        search_repository=search_repository,
        embedding_factory=embedding_factory,
        similarity_threshold=0.5,
        candidate_multiplier=4,
    )

    response = asyncio.run(
        service.search_many(
            user_id=user_id,
            knowledge_bases=[first, second],
            request=KnowledgeSearchRequest(query="项目会议如何准备", top_k=2),
        )
    )

    embedding_factory.create_service.assert_called_once_with(
        EmbeddingProfile(
            provider="mock",
            model="mock-hash-1024",
            dimension=2,
        )
    )
    embedding_service.embed_texts.assert_awaited_once_with(["项目会议如何准备"])
    search_repository.search_candidates_across_knowledge_bases.assert_awaited_once_with(
        user_id=user_id,
        knowledge_base_ids=[first.id, second.id],
        query_vector=(1.0, 0.0),
        embedding_model="mock-hash-1024",
        candidate_limit=8,
    )
    assert response.searched_knowledge_base_ids == [first.id, second.id]
    assert [result.score for result in response.results] == [0.93, 0.81]
    assert [result.knowledge_base_name for result in response.results] == [
        "项目规范",
        "会议资料",
    ]
    assert response.similarity_threshold == 0.5


def test_core_search_rejects_mixed_profiles_before_embedding() -> None:
    """不同模型的相似度不可直接比较，必须在产生费用前拒绝混合检索。"""
    user_id = uuid4()
    first = build_knowledge_base(user_id=user_id)
    second = build_knowledge_base(
        user_id=user_id,
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-v4",
    )
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        list_incompatible_ready_knowledge_base_ids=AsyncMock(),
        search_candidates_across_knowledge_bases=AsyncMock(),
    )
    service = build_core_search_service(
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    error = run_business_error(
        service.search_many(
            user_id=user_id,
            knowledge_bases=[first, second],
            request=KnowledgeSearchRequest(query="项目会议"),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_SEARCH_CONFIG_MISMATCH
    assert error.status_code == 409
    assert "不同的向量配置" in error.message
    embedding_factory.create_service.assert_not_called()
    search_repository.list_incompatible_ready_knowledge_base_ids.assert_not_awaited()


def test_core_search_rejects_cross_user_entity_before_database_query() -> None:
    """即使内部调用误传其他用户实体，核心检索也要在 SQL 前再次阻断。"""
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        list_incompatible_ready_knowledge_base_ids=AsyncMock(),
        search_candidates_across_knowledge_bases=AsyncMock(),
    )
    service = build_core_search_service(
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    error = run_business_error(
        service.search_many(
            user_id=uuid4(),
            knowledge_bases=[build_knowledge_base(user_id=uuid4())],
            request=KnowledgeSearchRequest(query="其他用户资料"),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND
    assert error.status_code == 404
    embedding_factory.create_service.assert_not_called()
    search_repository.list_incompatible_ready_knowledge_base_ids.assert_not_awaited()


def test_core_search_rejects_incompatible_stored_vectors_before_embedding() -> None:
    """只要一个活动知识库混入旧模型向量，就不能返回不完整或不可比较的证据。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        list_incompatible_ready_knowledge_base_ids=AsyncMock(
            return_value=[knowledge_base.id]
        ),
        search_candidates_across_knowledge_bases=AsyncMock(),
    )
    service = build_core_search_service(
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    error = run_business_error(
        service.search_many(
            user_id=user_id,
            knowledge_bases=[knowledge_base],
            request=KnowledgeSearchRequest(query="项目会议"),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_SEARCH_CONFIG_MISMATCH
    assert error.status_code == 409
    embedding_factory.create_service.assert_not_called()
    search_repository.search_candidates_across_knowledge_bases.assert_not_awaited()


def test_core_search_empty_selection_does_not_call_embedding_or_database() -> None:
    """没有活动知识库时返回空证据，不应产生模型调用或无意义向量 SQL。"""
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        list_incompatible_ready_knowledge_base_ids=AsyncMock(),
        search_candidates_across_knowledge_bases=AsyncMock(),
    )
    service = build_core_search_service(
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    response = asyncio.run(
        service.search_many(
            user_id=uuid4(),
            knowledge_bases=[],
            request=KnowledgeSearchRequest(query="项目会议", top_k=3),
        )
    )

    assert response.results == []
    assert response.searched_knowledge_base_ids == []
    embedding_factory.create_service.assert_not_called()
    search_repository.list_incompatible_ready_knowledge_base_ids.assert_not_awaited()
    search_repository.search_candidates_across_knowledge_bases.assert_not_awaited()


def test_conversation_search_hides_missing_or_cross_user_conversation() -> None:
    """会话越权和不存在统一返回 404，并且不能读取绑定或生成查询向量。"""
    binding_repository = SimpleNamespace(list_for_conversation_user=AsyncMock())
    core_search_service = SimpleNamespace(search_many=AsyncMock())
    service = build_conversation_search_service(
        conversation=None,
        binding_repository=binding_repository,
        core_search_service=core_search_service,
    )

    error = run_business_error(
        service.search(
            user_id=uuid4(),
            conversation_id=uuid4(),
            request=KnowledgeSearchRequest(query="机密项目"),
        )
    )

    assert error.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert error.status_code == 404
    binding_repository.list_for_conversation_user.assert_not_awaited()
    core_search_service.search_many.assert_not_awaited()


def test_conversation_search_skips_archived_bindings_and_returns_trace_state() -> None:
    """归档绑定不参与聊天检索，但结果应记录搜索和跳过了哪些知识库。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    active = build_knowledge_base(user_id=user_id, name="活动资料")
    archived = build_knowledge_base(
        user_id=user_id,
        name="旧资料",
        is_archived=True,
    )
    request = KnowledgeSearchRequest(query="会议目标", top_k=3)
    core_response = MultiKnowledgeSearchResponse(
        query=request.query,
        searched_knowledge_base_ids=[active.id],
        similarity_threshold=0.2,
        results=[
            build_candidate(
                knowledge_base=active,
                score=0.88,
                content_hash="d" * 64,
                chunk_index=0,
            )
        ],
    )
    core_search_service = SimpleNamespace(
        search_many=AsyncMock(return_value=core_response)
    )
    binding_repository = SimpleNamespace(
        list_for_conversation_user=AsyncMock(return_value=[active, archived])
    )
    service = build_conversation_search_service(
        conversation=conversation,
        binding_repository=binding_repository,
        core_search_service=core_search_service,
    )

    response = asyncio.run(
        service.search(
            user_id=user_id,
            conversation_id=conversation.id,
            request=request,
        )
    )

    core_search_service.search_many.assert_awaited_once_with(
        user_id=user_id,
        knowledge_bases=[active],
        request=request,
    )
    assert response.conversation_id == conversation.id
    assert response.searched_knowledge_base_ids == [active.id]
    assert response.skipped_archived_knowledge_base_ids == [archived.id]
    assert response.results[0].knowledge_base_id == active.id


def build_core_search_service(
    search_repository,
    embedding_factory,
    similarity_threshold: float = 0.2,
    candidate_multiplier: int = 4,
) -> KnowledgeSearchService:
    """构造不连接真实基础设施的核心跨库检索 Service。"""
    service = object.__new__(KnowledgeSearchService)
    service.search_repository = search_repository
    service.embedding_factory = embedding_factory
    service.similarity_threshold = similarity_threshold
    service.candidate_multiplier = candidate_multiplier
    return service


def build_conversation_search_service(
    conversation: ConversationEntity | None,
    binding_repository,
    core_search_service,
) -> ConversationKnowledgeSearchService:
    """构造会话绑定到核心检索的薄编排 Service。"""
    service = object.__new__(ConversationKnowledgeSearchService)
    service.conversation_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation)
    )
    service.binding_repository = binding_repository
    service.knowledge_search_service = core_search_service
    return service


def build_conversation(user_id: UUID) -> ConversationEntity:
    """构造测试所需的最小会话实体。"""
    return ConversationEntity(
        id=uuid4(),
        user_id=user_id,
        title="项目助手",
        model_name="mock",
        temperature=0.7,
        total_tokens=0,
        is_archived=False,
    )


def build_knowledge_base(
    user_id: UUID,
    name: str = "项目资料",
    is_archived: bool = False,
    embedding_provider: str = "mock",
    embedding_model: str = "mock-hash-1024",
) -> KnowledgeBaseEntity:
    """构造带冻结 Embedding 配置的知识库。"""
    return KnowledgeBaseEntity(
        id=uuid4(),
        user_id=user_id,
        name=name,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=2,
        is_archived=is_archived,
    )


def build_candidate(
    knowledge_base: KnowledgeBaseEntity,
    score: float,
    content_hash: str,
    chunk_index: int,
) -> MultiKnowledgeSearchCandidate:
    """构造携带知识库身份和内容哈希的跨库候选。"""
    return MultiKnowledgeSearchCandidate(
        knowledge_base_id=knowledge_base.id,
        knowledge_base_name=knowledge_base.name,
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=f"{knowledge_base.name}.md",
        chunk_index=chunk_index,
        content=f"{knowledge_base.name}第 {chunk_index} 个会议片段",
        content_hash=content_hash,
        heading_path="项目 > 会议",
        page_start=None,
        page_end=None,
        token_count=12,
        score=score,
    )


def run_business_error(awaitable) -> BusinessException:
    """运行异步操作并返回测试预期的业务异常。"""

    async def scenario() -> BusinessException:
        """捕获业务异常，避免每个测试重复编写 try/except。"""
        try:
            await awaitable
        except BusinessException as exc:
            return exc
        raise AssertionError("预期操作抛出 BusinessException")

    return asyncio.run(scenario())
