import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.main import create_app
from app.modules.identity.dependencies import get_current_user
from app.modules.knowledge.api.knowledge_base_router import create_knowledge_base
from app.modules.knowledge.constants import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.dependencies import get_knowledge_base_service
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.schema.knowledge_base_schema import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.modules.knowledge.service.knowledge_base_service import KnowledgeBaseService


def test_create_schema_strips_text_and_converts_blank_description() -> None:
    """创建请求应清理用户输入，避免空格名称和无意义空描述进入数据库。"""
    request = KnowledgeBaseCreate(name="  项目资料  ", description="   ")

    assert request.name == "项目资料"
    assert request.description is None


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"description": "x" * 4001},
    ],
)
def test_create_schema_rejects_invalid_fields(payload: dict) -> None:
    """创建知识库时应拒绝空名称和过长描述。"""
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": None}, {"name": "   "}, {"is_archived": None}],
)
def test_update_schema_rejects_empty_or_invalid_changes(payload: dict) -> None:
    """PATCH 必须至少包含一个有效字段，且非空字段不能显式传 null。"""
    with pytest.raises(ValidationError):
        KnowledgeBaseUpdate.model_validate(payload)


def test_update_schema_allows_null_description_to_clear_it() -> None:
    """描述是可空字段，因此显式 null 表示清空已有描述。"""
    request = KnowledgeBaseUpdate(description=None)

    assert request.model_dump(exclude_unset=True) == {"description": None}


def test_repository_owned_lookup_filters_id_and_user() -> None:
    """知识库详情查询必须同时包含知识库 ID 和当前用户 ID。"""
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    repository = KnowledgeBaseRepository(db)

    asyncio.run(repository.get_by_id_for_user(uuid4(), uuid4()))

    statement = db.scalar.await_args.args[0]
    where_sql = str(statement.whereclause)
    assert "knowledge_bases.id" in where_sql
    assert "knowledge_bases.user_id" in where_sql


def test_repository_list_filters_owner_and_archived_state() -> None:
    """默认列表查询只能返回当前用户未归档的知识库。"""
    scalar_result = SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = KnowledgeBaseRepository(db)

    asyncio.run(repository.list_by_user(uuid4(), include_archived=False))

    statement = db.scalars.await_args.args[0]
    where_sql = str(statement.whereclause)
    assert "knowledge_bases.user_id" in where_sql
    assert "knowledge_bases.is_archived" in where_sql


def test_service_hides_foreign_knowledge_base_as_not_found() -> None:
    """不存在和不属于当前用户的知识库应返回完全相同的 404。"""
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=None)
    )

    async def scenario() -> BusinessException:
        """捕获所有权查询失败后产生的稳定业务异常。"""
        try:
            await service.get(user_id=uuid4(), knowledge_base_id=uuid4())
        except BusinessException as exc:
            return exc
        raise AssertionError("跨用户访问必须被拒绝")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND
    assert error.status_code == 404


def test_service_create_uses_embedding_snapshot_and_commits() -> None:
    """创建时应冻结 Embedding 配置快照，并由 Service 提交事务。"""
    user_id = uuid4()
    entity = build_knowledge_base(user_id=user_id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_name_for_user=AsyncMock(return_value=None),
        create=AsyncMock(return_value=entity),
    )
    request = KnowledgeBaseCreate(name="项目资料", description="会议相关文档")

    response = asyncio.run(service.create(user_id=user_id, request=request))

    assert response.id == entity.id
    service.repository.create.assert_awaited_once_with(
        user_id=user_id,
        request=request,
        embedding_provider=DEFAULT_EMBEDDING_PROVIDER,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
    )
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


def test_service_rejects_existing_name_before_insert() -> None:
    """同一用户已有同名知识库时，不应继续执行数据库插入。"""
    user_id = uuid4()
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_name_for_user=AsyncMock(
            return_value=build_knowledge_base(user_id=user_id)
        ),
        create=AsyncMock(),
    )

    async def scenario() -> BusinessException:
        """捕获同名预检查产生的冲突异常。"""
        try:
            await service.create(
                user_id=user_id,
                request=KnowledgeBaseCreate(name="项目资料"),
            )
        except BusinessException as exc:
            return exc
        raise AssertionError("同名知识库必须被拒绝")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NAME_CONFLICT
    assert error.status_code == 409
    service.repository.create.assert_not_awaited()


def test_service_maps_unique_constraint_race_and_rolls_back() -> None:
    """预检查后发生并发同名插入时，数据库约束仍应映射为 409 并回滚。"""
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db)
    unique_violation = Exception("unique")
    unique_violation.diag = SimpleNamespace(
        constraint_name="uq_knowledge_bases_user_name"
    )
    service.repository = SimpleNamespace(
        get_by_name_for_user=AsyncMock(return_value=None),
        create=AsyncMock(side_effect=IntegrityError("INSERT", {}, unique_violation)),
    )

    async def scenario() -> BusinessException:
        """捕获唯一约束竞争产生的稳定业务异常。"""
        try:
            await service.create(
                user_id=uuid4(),
                request=KnowledgeBaseCreate(name="项目资料"),
            )
        except BusinessException as exc:
            return exc
        raise AssertionError("唯一约束冲突必须转换成业务异常")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NAME_CONFLICT
    assert error.status_code == 409
    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()


def test_service_rolls_back_unexpected_commit_failure() -> None:
    """未知数据库提交错误必须回滚并原样抛出，不能伪装成业务成功。"""
    user_id = uuid4()
    entity = build_knowledge_base(user_id=user_id)
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("database unavailable")),
        rollback=AsyncMock(),
    )
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_name_for_user=AsyncMock(return_value=None),
        create=AsyncMock(return_value=entity),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.create(
                user_id=user_id,
                request=KnowledgeBaseCreate(name="项目资料"),
            )
        )

    db.rollback.assert_awaited_once_with()


def test_service_update_only_applies_requested_fields() -> None:
    """修改知识库时只改请求出现的字段，并在成功后提交事务。"""
    user_id = uuid4()
    entity = build_knowledge_base(user_id=user_id)
    original_name = entity.name
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=entity),
        update=AsyncMock(side_effect=lambda knowledge_base: knowledge_base),
    )

    response = asyncio.run(
        service.update(
            user_id=user_id,
            knowledge_base_id=entity.id,
            changes=KnowledgeBaseUpdate(description=None, is_archived=True),
        )
    )

    assert response.name == original_name
    assert response.description is None
    assert response.is_archived is True
    db.commit.assert_awaited_once_with()


def test_service_archives_instead_of_physically_deleting() -> None:
    """DELETE 接口应执行可恢复的软归档，避免未来留下孤立原文件。"""
    user_id = uuid4()
    entity = build_knowledge_base(user_id=user_id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), delete=AsyncMock())
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=entity),
        update=AsyncMock(side_effect=lambda knowledge_base: knowledge_base),
    )

    asyncio.run(service.archive(user_id=user_id, knowledge_base_id=entity.id))

    assert entity.is_archived is True
    service.repository.update.assert_awaited_once_with(entity)
    db.commit.assert_awaited_once_with()
    db.delete.assert_not_awaited()


def test_service_repeated_archive_is_idempotent() -> None:
    """已经归档的知识库再次 DELETE 时不应重复写库或修改更新时间。"""
    user_id = uuid4()
    entity = build_knowledge_base(user_id=user_id)
    entity.is_archived = True
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=entity),
        update=AsyncMock(),
    )

    asyncio.run(service.archive(user_id=user_id, knowledge_base_id=entity.id))

    service.repository.update.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_create_route_uses_authenticated_user() -> None:
    """创建接口必须使用 JWT 恢复出的用户 ID，而不能相信客户端传入所有者。"""
    current_user = SimpleNamespace(id=uuid4())
    expected = KnowledgeBaseResponse.model_validate(
        build_knowledge_base(user_id=current_user.id)
    )
    service = SimpleNamespace(create=AsyncMock(return_value=expected))
    request = KnowledgeBaseCreate(name="项目资料")

    response = asyncio.run(
        create_knowledge_base(
            request=request,
            service=service,
            current_user=current_user,
        )
    )

    assert response == expected
    service.create.assert_awaited_once_with(
        user_id=current_user.id,
        request=request,
    )


def test_knowledge_base_openapi_is_versioned_and_protected() -> None:
    """知识库 CRUD 应位于 v1 路径，并全部声明 Bearer Token 安全要求。"""
    paths = create_app().openapi()["paths"]
    collection = paths["/api/v1/knowledge-bases"]
    detail = paths["/api/v1/knowledge-bases/{knowledge_base_id}"]

    assert {"get", "post"}.issubset(collection)
    assert {"get", "patch", "delete"}.issubset(detail)
    assert collection["post"]["security"]
    assert detail["delete"]["security"]


def test_create_http_endpoint_returns_201_and_response_contract() -> None:
    """通过真实 ASGI 路由验证请求体、依赖注入、状态码和响应序列化。"""
    current_user = SimpleNamespace(id=uuid4())
    entity = build_knowledge_base(user_id=current_user.id)
    service = SimpleNamespace(
        create=AsyncMock(return_value=KnowledgeBaseResponse.model_validate(entity))
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_knowledge_base_service] = lambda: service

    async def scenario() -> tuple[int, dict]:
        """向内存中的 FastAPI 应用发起一次完整 HTTP 请求。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/knowledge-bases",
                json={"name": "  项目资料  ", "description": "会议相关文档"},
            )
        return response.status_code, response.json()

    try:
        status_code, payload = asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()

    assert status_code == 201
    assert payload["id"] == str(entity.id)
    assert payload["embedding_dimension"] == DEFAULT_EMBEDDING_DIMENSION
    normalized_request = service.create.await_args.kwargs["request"]
    assert normalized_request.name == "项目资料"
    assert service.create.await_args.kwargs["user_id"] == current_user.id


def build_service(db=None) -> KnowledgeBaseService:
    """构造不连接真实数据库的知识库 Service 测试对象。"""
    service = object.__new__(KnowledgeBaseService)
    service.db = db or SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    return service


def build_knowledge_base(user_id: UUID) -> KnowledgeBaseEntity:
    """构造字段完整、可直接转换为响应模型的知识库实体。"""
    now = datetime.now(timezone.utc)
    return KnowledgeBaseEntity(
        id=uuid4(),
        user_id=user_id,
        name="项目资料",
        description="会议相关文档",
        embedding_provider=DEFAULT_EMBEDDING_PROVIDER,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedding_dimension=DEFAULT_EMBEDDING_DIMENSION,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
