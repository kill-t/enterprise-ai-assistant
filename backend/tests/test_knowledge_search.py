import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.common.enums import DocumentStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.schemas import EmbeddingProfile, EmbeddingResult
from app.main import create_app
from app.modules.identity.dependencies import get_current_user
from app.modules.knowledge.dependencies import get_knowledge_search_service
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.knowledge_search_repository import (
    KnowledgeSearchRepository,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.modules.knowledge.search_models import KnowledgeSearchCandidate
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


def test_search_request_strips_query_and_rejects_invalid_limits() -> None:
    """查询文本应先清理空格，空问题和过大的 Top-K 必须在路由前被拒绝。"""
    request = KnowledgeSearchRequest(query="  项目会议安排  ", top_k=3)

    assert request.query == "项目会议安排"
    assert request.top_k == 3

    for invalid_payload in (
        {"query": "   "},
        {"query": "有效问题", "top_k": 0},
        {"query": "有效问题", "top_k": 21},
    ):
        with pytest.raises(ValidationError):
            KnowledgeSearchRequest.model_validate(invalid_payload)


def test_repository_query_contains_ownership_ready_and_model_guards() -> None:
    """向量 SQL 必须同时限制所有者、READY 文档和冻结的 Embedding 模型。"""
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)))
    repository = KnowledgeSearchRepository(db)
    user_id = uuid4()
    knowledge_base_id = uuid4()

    results = asyncio.run(
        repository.search_candidates(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            query_vector=(1.0, 0.0),
            embedding_model="mock-hash-1024",
            candidate_limit=12,
        )
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    params = statement.compile(dialect=postgresql.dialect()).params

    assert results == []
    assert "knowledge_bases.user_id" in sql
    assert "knowledge_bases.is_archived IS false" in sql
    assert "documents.status" in sql
    assert "document_chunks.embedding_model" in sql
    assert "<=>" in sql
    assert "ORDER BY" in sql
    assert params["user_id_1"] == user_id
    assert params["id_1"] == knowledge_base_id
    assert params["status_1"] == DocumentStatus.READY
    assert params["embedding_model_1"] == "mock-hash-1024"
    assert 12 in params.values()


def test_service_builds_query_vector_then_filters_threshold_and_top_k() -> None:
    """Service 负责按知识库快照生成查询向量，并执行可靠性阈值和数量规则。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    embedding_service = SimpleNamespace(
        embed_texts=AsyncMock(
            return_value=EmbeddingResult(
                vectors=((1.0, 0.0),),
                model=knowledge_base.embedding_model,
                dimension=knowledge_base.embedding_dimension,
                input_tokens=4,
            )
        )
    )
    embedding_factory = SimpleNamespace(
        create_service=Mock(return_value=embedding_service)
    )
    candidates = [
        build_candidate(score=0.92, chunk_index=0),
        build_candidate(score=0.61, chunk_index=1),
        build_candidate(score=0.49, chunk_index=2),
    ]
    search_repository = SimpleNamespace(
        has_incompatible_ready_chunks=AsyncMock(return_value=False),
        search_candidates=AsyncMock(return_value=candidates),
    )
    service = build_service(
        knowledge_base=knowledge_base,
        search_repository=search_repository,
        embedding_factory=embedding_factory,
        similarity_threshold=0.5,
        candidate_multiplier=4,
    )

    response = asyncio.run(
        service.search(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            request=KnowledgeSearchRequest(query="项目会议", top_k=2),
        )
    )

    embedding_factory.create_service.assert_called_once_with(
        EmbeddingProfile(
            provider=knowledge_base.embedding_provider,
            model=knowledge_base.embedding_model,
            dimension=knowledge_base.embedding_dimension,
        )
    )
    embedding_service.embed_texts.assert_awaited_once_with(["项目会议"])
    search_repository.search_candidates.assert_awaited_once_with(
        user_id=user_id,
        knowledge_base_id=knowledge_base.id,
        query_vector=(1.0, 0.0),
        embedding_model=knowledge_base.embedding_model,
        candidate_limit=8,
    )
    assert [result.score for result in response.results] == [0.92, 0.61]
    assert response.similarity_threshold == 0.5
    assert response.embedding_model == knowledge_base.embedding_model


def test_service_hides_missing_or_cross_user_knowledge_base() -> None:
    """知识库不存在或属于其他用户时必须统一返回 404，且不能调用向量模型。"""
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        has_incompatible_ready_chunks=AsyncMock(),
        search_candidates=AsyncMock(),
    )
    service = build_service(
        knowledge_base=None,
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    error = run_business_error(
        service.search(
            user_id=uuid4(),
            knowledge_base_id=uuid4(),
            request=KnowledgeSearchRequest(query="机密项目"),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND
    assert error.status_code == 404
    embedding_factory.create_service.assert_not_called()
    search_repository.search_candidates.assert_not_awaited()


def test_service_rejects_archived_or_incompatible_knowledge_base() -> None:
    """归档库和混入旧模型向量的库都不能继续产生不可靠检索结果。"""
    user_id = uuid4()
    archived = build_knowledge_base(user_id=user_id, is_archived=True)
    archived_service = build_service(knowledge_base=archived)

    archived_error = run_business_error(
        archived_service.search(
            user_id=user_id,
            knowledge_base_id=archived.id,
            request=KnowledgeSearchRequest(query="项目会议"),
        )
    )

    assert archived_error.code == ErrorCode.KNOWLEDGE_BASE_ARCHIVED
    assert archived_error.status_code == 409

    active = build_knowledge_base(user_id=user_id)
    embedding_factory = SimpleNamespace(create_service=Mock())
    search_repository = SimpleNamespace(
        has_incompatible_ready_chunks=AsyncMock(return_value=True),
        search_candidates=AsyncMock(),
    )
    incompatible_service = build_service(
        knowledge_base=active,
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    incompatible_error = run_business_error(
        incompatible_service.search(
            user_id=user_id,
            knowledge_base_id=active.id,
            request=KnowledgeSearchRequest(query="项目会议"),
        )
    )

    assert incompatible_error.code == ErrorCode.KNOWLEDGE_SEARCH_CONFIG_MISMATCH
    assert incompatible_error.status_code == 409
    embedding_factory.create_service.assert_not_called()
    search_repository.search_candidates.assert_not_awaited()


def test_service_maps_embedding_failure_to_safe_503() -> None:
    """向量服务失败应返回稳定错误，不泄露第三方响应或继续查询数据库。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    embedding_service = SimpleNamespace(
        embed_texts=AsyncMock(
            side_effect=EmbeddingError(
                code=EmbeddingErrorCode.PROVIDER_REQUEST_FAILED,
                message="包含第三方内部细节",
            )
        )
    )
    embedding_factory = SimpleNamespace(
        create_service=Mock(return_value=embedding_service)
    )
    search_repository = SimpleNamespace(
        has_incompatible_ready_chunks=AsyncMock(return_value=False),
        search_candidates=AsyncMock(),
    )
    service = build_service(
        knowledge_base=knowledge_base,
        search_repository=search_repository,
        embedding_factory=embedding_factory,
    )

    error = run_business_error(
        service.search(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            request=KnowledgeSearchRequest(query="项目会议"),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE
    assert error.status_code == 503
    assert "第三方内部细节" not in error.message
    search_repository.search_candidates.assert_not_awaited()


def test_search_http_endpoint_is_authenticated_and_returns_sources() -> None:
    """真实 ASGI 路由应使用当前用户，并返回可追溯的结构化来源。"""
    user_id = uuid4()
    knowledge_base_id = uuid4()
    request = KnowledgeSearchRequest(query="项目会议", top_k=2)
    expected = KnowledgeSearchResponse(
        knowledge_base_id=knowledge_base_id,
        query=request.query,
        embedding_model="mock-hash-1024",
        similarity_threshold=0.2,
        results=[build_candidate(score=0.88, chunk_index=0)],
    )
    service = SimpleNamespace(search=AsyncMock(return_value=expected))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    app.dependency_overrides[get_knowledge_search_service] = lambda: service

    async def scenario() -> tuple[int, dict]:
        """通过内存 ASGI 应用执行一次带认证依赖的检索请求。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/search",
                json=request.model_dump(),
            )
        return response.status_code, response.json()

    try:
        status_code, payload = asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()

    assert status_code == 200
    assert payload["results"][0]["filename"] == "meeting-notes.md"
    assert payload["results"][0]["score"] == 0.88
    service.search.assert_awaited_once_with(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        request=request,
    )

    operation = create_app().openapi()["paths"][
        "/api/v1/knowledge-bases/{knowledge_base_id}/search"
    ]["post"]
    assert operation["security"]


def build_service(
    knowledge_base: KnowledgeBaseEntity | None,
    search_repository=None,
    embedding_factory=None,
    similarity_threshold: float = 0.2,
    candidate_multiplier: int = 4,
) -> KnowledgeSearchService:
    """构造不连接真实基础设施的检索 Service。"""
    service = object.__new__(KnowledgeSearchService)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.search_repository = search_repository or SimpleNamespace(
        has_incompatible_ready_chunks=AsyncMock(return_value=False),
        search_candidates=AsyncMock(return_value=[]),
    )
    service.embedding_factory = embedding_factory or SimpleNamespace(
        create_service=Mock()
    )
    service.similarity_threshold = similarity_threshold
    service.candidate_multiplier = candidate_multiplier
    return service


def build_knowledge_base(
    user_id: UUID,
    is_archived: bool = False,
) -> KnowledgeBaseEntity:
    """构造带 Embedding 快照的知识库实体。"""
    return KnowledgeBaseEntity(
        id=uuid4(),
        user_id=user_id,
        name="项目资料",
        embedding_provider="mock",
        embedding_model="mock-hash-1024",
        embedding_dimension=2,
        is_archived=is_archived,
    )


def build_candidate(score: float, chunk_index: int) -> KnowledgeSearchCandidate:
    """构造携带来源信息的候选 Chunk。"""
    return KnowledgeSearchCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="meeting-notes.md",
        chunk_index=chunk_index,
        content=f"第 {chunk_index} 个项目会议片段",
        heading_path="企业项目会议 > 基本信息",
        page_start=None,
        page_end=None,
        token_count=12,
        score=score,
    )


def run_business_error(awaitable) -> BusinessException:
    """运行异步检索并返回测试预期的业务异常。"""

    async def scenario() -> BusinessException:
        """捕获异常，避免每个测试重复编写 try/except。"""
        try:
            await awaitable
        except BusinessException as exc:
            return exc
        raise AssertionError("预期检索抛出 BusinessException")

    return asyncio.run(scenario())
