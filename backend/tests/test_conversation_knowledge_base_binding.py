import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.main import create_app
from app.modules.conversation.dependencies import (
    get_conversation_knowledge_base_service,
)
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.repository.conversation_knowledge_base_repository import (
    ConversationKnowledgeBaseRepository,
)
from app.modules.conversation.schema.conversation_knowledge_base_schema import (
    ConversationKnowledgeBaseSelectionResponse,
    ConversationKnowledgeBaseUpdate,
)
from app.modules.conversation.service.conversation_knowledge_base_service import (
    ConversationKnowledgeBaseService,
)
from app.modules.identity.dependencies import get_current_user
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity


def test_binding_request_rejects_duplicates_and_more_than_five() -> None:
    """客户端必须显式提交无重复且数量受限的知识库 ID 列表。"""
    first_id = uuid4()

    assert ConversationKnowledgeBaseUpdate(knowledge_base_ids=[]).knowledge_base_ids == []

    with pytest.raises(ValidationError, match="不能重复"):
        ConversationKnowledgeBaseUpdate(
            knowledge_base_ids=[first_id, first_id]
        )

    with pytest.raises(ValidationError):
        ConversationKnowledgeBaseUpdate(
            knowledge_base_ids=[uuid4() for _ in range(6)]
        )


def test_binding_repository_list_query_filters_both_resource_owners() -> None:
    """读取绑定的 SQL 必须同时限制会话所有者和知识库所有者。"""
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    )
    repository = ConversationKnowledgeBaseRepository(db)
    user_id = uuid4()
    conversation_id = uuid4()

    result = asyncio.run(
        repository.list_for_conversation_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
    )

    statement = db.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert result == []
    assert "conversations.user_id" in sql
    assert "knowledge_bases.user_id" in sql
    assert "conversation_knowledge_bases" in sql
    assert "ORDER BY knowledge_bases.name" in sql


def test_binding_repository_replaces_all_rows_without_committing() -> None:
    """Repository 只执行删除和新增，事务提交必须留给 Service。"""
    db = SimpleNamespace(
        execute=AsyncMock(),
        add_all=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    repository = ConversationKnowledgeBaseRepository(db)
    conversation_id = uuid4()
    knowledge_base_ids = [uuid4(), uuid4()]

    asyncio.run(
        repository.replace(
            conversation_id=conversation_id,
            knowledge_base_ids=knowledge_base_ids,
        )
    )

    delete_statement = db.execute.await_args.args[0]
    delete_sql = str(delete_statement.compile(dialect=postgresql.dialect()))
    entities = db.add_all.call_args.args[0]

    assert "DELETE FROM conversation_knowledge_bases" in delete_sql
    assert [entity.knowledge_base_id for entity in entities] == knowledge_base_ids
    assert all(entity.conversation_id == conversation_id for entity in entities)
    db.flush.assert_awaited_once_with()
    db.commit.assert_not_awaited()


def test_service_hides_missing_or_cross_user_conversation() -> None:
    """会话不存在或越权时统一返回 404，且不能继续读取绑定。"""
    binding_repository = SimpleNamespace(
        list_for_conversation_user=AsyncMock(),
        replace=AsyncMock(),
    )
    service = build_service(
        conversation=None,
        binding_repository=binding_repository,
    )

    error = run_business_error(
        service.get_selection(user_id=uuid4(), conversation_id=uuid4())
    )

    assert error.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert error.status_code == 404
    binding_repository.list_for_conversation_user.assert_not_awaited()


def test_replace_rejects_any_missing_or_cross_user_knowledge_base() -> None:
    """批量 ID 只要有一个不属于当前用户，就必须整体失败且不删除旧绑定。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    requested_ids = [uuid4(), uuid4()]
    knowledge_base_repository = SimpleNamespace(
        list_by_ids_for_user=AsyncMock(
            return_value=[
                build_knowledge_base(user_id=user_id, knowledge_base_id=requested_ids[0])
            ]
        )
    )
    binding_repository = SimpleNamespace(replace=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(
        conversation=conversation,
        db=db,
        binding_repository=binding_repository,
        knowledge_base_repository=knowledge_base_repository,
    )

    error = run_business_error(
        service.replace_selection(
            user_id=user_id,
            conversation_id=conversation.id,
            request=ConversationKnowledgeBaseUpdate(
                knowledge_base_ids=requested_ids
            ),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND
    assert error.status_code == 404
    binding_repository.replace.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_replace_rejects_archived_knowledge_base_before_writing() -> None:
    """归档知识库不能新绑定，已有绑定仍可通过 GET 展示后再移除。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    archived = build_knowledge_base(user_id=user_id, is_archived=True)
    knowledge_base_repository = SimpleNamespace(
        list_by_ids_for_user=AsyncMock(return_value=[archived])
    )
    binding_repository = SimpleNamespace(replace=AsyncMock())
    service = build_service(
        conversation=conversation,
        binding_repository=binding_repository,
        knowledge_base_repository=knowledge_base_repository,
    )

    error = run_business_error(
        service.replace_selection(
            user_id=user_id,
            conversation_id=conversation.id,
            request=ConversationKnowledgeBaseUpdate(
                knowledge_base_ids=[archived.id]
            ),
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_ARCHIVED
    assert error.status_code == 409
    binding_repository.replace.assert_not_awaited()


def test_replace_is_atomic_and_preserves_request_order_in_response() -> None:
    """全部校验通过后才能原子替换，响应顺序与用户提交顺序一致。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    first = build_knowledge_base(user_id=user_id, name="第一知识库")
    second = build_knowledge_base(user_id=user_id, name="第二知识库")
    request = ConversationKnowledgeBaseUpdate(
        knowledge_base_ids=[second.id, first.id]
    )
    knowledge_base_repository = SimpleNamespace(
        # 模拟数据库返回顺序与请求不同，Service 必须重建用户选择顺序。
        list_by_ids_for_user=AsyncMock(return_value=[first, second])
    )
    binding_repository = SimpleNamespace(replace=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(
        conversation=conversation,
        db=db,
        binding_repository=binding_repository,
        knowledge_base_repository=knowledge_base_repository,
    )

    response = asyncio.run(
        service.replace_selection(
            user_id=user_id,
            conversation_id=conversation.id,
            request=request,
        )
    )

    knowledge_base_repository.list_by_ids_for_user.assert_awaited_once_with(
        user_id=user_id,
        knowledge_base_ids=request.knowledge_base_ids,
        for_update=True,
    )
    binding_repository.replace.assert_awaited_once_with(
        conversation_id=conversation.id,
        knowledge_base_ids=request.knowledge_base_ids,
    )
    db.commit.assert_awaited_once_with()
    assert [item.id for item in response.knowledge_bases] == [second.id, first.id]


def test_replace_empty_selection_unbinds_all_without_querying_knowledge_bases() -> None:
    """空数组表示明确解绑全部，且不应生成无意义的知识库 IN 查询。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    knowledge_base_repository = SimpleNamespace(
        list_by_ids_for_user=AsyncMock()
    )
    binding_repository = SimpleNamespace(replace=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(
        conversation=conversation,
        db=db,
        binding_repository=binding_repository,
        knowledge_base_repository=knowledge_base_repository,
    )

    response = asyncio.run(
        service.replace_selection(
            user_id=user_id,
            conversation_id=conversation.id,
            request=ConversationKnowledgeBaseUpdate(knowledge_base_ids=[]),
        )
    )

    knowledge_base_repository.list_by_ids_for_user.assert_not_awaited()
    binding_repository.replace.assert_awaited_once_with(
        conversation_id=conversation.id,
        knowledge_base_ids=[],
    )
    db.commit.assert_awaited_once_with()
    assert response.knowledge_bases == []


def test_replace_rolls_back_when_commit_fails() -> None:
    """删除旧绑定或新增绑定的提交失败时，整个替换事务必须回滚。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    knowledge_base = build_knowledge_base(user_id=user_id)
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=RuntimeError("database unavailable")),
        rollback=AsyncMock(),
    )
    service = build_service(
        conversation=conversation,
        db=db,
        binding_repository=SimpleNamespace(replace=AsyncMock()),
        knowledge_base_repository=SimpleNamespace(
            list_by_ids_for_user=AsyncMock(return_value=[knowledge_base])
        ),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.replace_selection(
                user_id=user_id,
                conversation_id=conversation.id,
                request=ConversationKnowledgeBaseUpdate(
                    knowledge_base_ids=[knowledge_base.id]
                ),
            )
        )

    db.rollback.assert_awaited_once_with()


def test_binding_http_get_and_put_are_authenticated() -> None:
    """GET/PUT 路由必须使用 JWT 当前用户，并返回统一选择结构。"""
    user_id = uuid4()
    conversation_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    expected = ConversationKnowledgeBaseSelectionResponse(
        conversation_id=conversation_id,
        knowledge_bases=[knowledge_base],
    )
    service = SimpleNamespace(
        get_selection=AsyncMock(return_value=expected),
        replace_selection=AsyncMock(return_value=expected),
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    app.dependency_overrides[get_conversation_knowledge_base_service] = (
        lambda: service
    )

    async def scenario() -> tuple[int, dict, int, dict]:
        """通过内存 ASGI 应用分别执行读取和替换请求。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            get_response = await client.get(
                f"/api/v1/conversations/{conversation_id}/knowledge-bases"
            )
            put_response = await client.put(
                f"/api/v1/conversations/{conversation_id}/knowledge-bases",
                json={"knowledge_base_ids": [str(knowledge_base.id)]},
            )
        return (
            get_response.status_code,
            get_response.json(),
            put_response.status_code,
            put_response.json(),
        )

    try:
        get_status, get_payload, put_status, put_payload = asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()

    assert get_status == 200
    assert put_status == 200
    assert get_payload["knowledge_bases"][0]["id"] == str(knowledge_base.id)
    assert put_payload["conversation_id"] == str(conversation_id)
    service.get_selection.assert_awaited_once_with(
        user_id=user_id,
        conversation_id=conversation_id,
    )
    put_request = service.replace_selection.await_args.kwargs["request"]
    assert put_request.knowledge_base_ids == [knowledge_base.id]

    operation = create_app().openapi()["paths"][
        "/api/v1/conversations/{conversation_id}/knowledge-bases"
    ]
    assert operation["get"]["security"]
    assert operation["put"]["security"]


def build_service(
    conversation: ConversationEntity | None,
    db=None,
    binding_repository=None,
    knowledge_base_repository=None,
) -> ConversationKnowledgeBaseService:
    """构造不连接真实数据库的会话知识库绑定 Service。"""
    service = object.__new__(ConversationKnowledgeBaseService)
    service.db = db or SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service.conversation_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation)
    )
    service.binding_repository = binding_repository or SimpleNamespace(
        list_for_conversation_user=AsyncMock(return_value=[]),
        replace=AsyncMock(),
    )
    service.knowledge_base_repository = knowledge_base_repository or SimpleNamespace(
        list_by_ids_for_user=AsyncMock(return_value=[])
    )
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
    knowledge_base_id: UUID | None = None,
    name: str = "项目资料",
    is_archived: bool = False,
) -> KnowledgeBaseEntity:
    """构造可转换为 API 响应的知识库实体。"""
    now = datetime.now(timezone.utc)
    return KnowledgeBaseEntity(
        id=knowledge_base_id or uuid4(),
        user_id=user_id,
        name=name,
        description=None,
        embedding_provider="mock",
        embedding_model="mock-hash-1024",
        embedding_dimension=1024,
        is_archived=is_archived,
        created_at=now,
        updated_at=now,
    )


def run_business_error(awaitable) -> BusinessException:
    """运行异步绑定操作并返回预期业务异常。"""

    async def scenario() -> BusinessException:
        """捕获业务异常，减少测试中的重复代码。"""
        try:
            await awaitable
        except BusinessException as exc:
            return exc
        raise AssertionError("预期绑定操作抛出 BusinessException")

    return asyncio.run(scenario())
