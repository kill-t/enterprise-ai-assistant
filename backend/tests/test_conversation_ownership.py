import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.main import create_app
from app.modules.conversation.api.conversation_router import create_conversation
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.modules.conversation.service.conversation_service import ConversationService


def test_conversation_user_id_has_database_foreign_key() -> None:
    """会话所有者字段必须由数据库外键约束到 users 表。"""
    foreign_keys = ConversationEntity.__table__.c.user_id.foreign_keys

    assert any(key.target_fullname == "users.id" for key in foreign_keys)


def test_conversation_create_uses_current_provider_model_by_default() -> None:
    """客户端省略模型名时，应跟随当前 Provider，而不是保存过期的硬编码模型。"""
    request = ConversationCreate(title="项目会议")

    assert request.model_name == ProviderFactory.get_model_name()


def test_repository_owned_lookup_filters_conversation_and_user() -> None:
    """所有权查询必须同时包含会话 ID 和当前用户 ID 条件。"""
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    repository = ConversationRepository(db)

    asyncio.run(repository.get_by_id_for_user(uuid4(), uuid4()))

    statement = db.scalar.await_args.args[0]
    where_sql = str(statement.whereclause)
    assert "conversations.id" in where_sql
    assert "conversations.user_id" in where_sql


def test_service_hides_foreign_conversation_as_not_found() -> None:
    """不属于当前用户的会话应与不存在会话返回相同 404。"""
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=None)
    )

    async def scenario() -> BusinessException:
        """捕获所有权校验失败后的业务异常。"""
        try:
            await service.get(user_id=uuid4(), conversation_id=uuid4())
        except BusinessException as exc:
            return exc
        raise AssertionError("跨用户访问必须被拒绝")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert error.status_code == 404


def test_service_lists_only_current_user_conversations() -> None:
    """会话列表必须把当前用户和归档过滤条件传给 Repository。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    service = build_service()
    service.repository = SimpleNamespace(
        list_by_user=AsyncMock(return_value=[conversation])
    )

    response = asyncio.run(
        service.list_conversations(user_id=user_id, include_archived=False)
    )

    assert response[0].id == conversation.id
    service.repository.list_by_user.assert_awaited_once_with(
        user_id=user_id,
        include_archived=False,
    )


def test_service_updates_owned_conversation_and_commits() -> None:
    """更新会话应只修改请求中出现的字段，并由 Service 提交事务。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation),
        update=AsyncMock(side_effect=lambda entity: entity),
    )

    response = asyncio.run(
        service.update(
            user_id=user_id,
            conversation_id=conversation.id,
            changes=ConversationUpdate(title="新的标题", is_archived=True),
        )
    )

    assert response.title == "新的标题"
    assert response.is_archived is True
    db.commit.assert_awaited_once_with()


def test_service_deletes_owned_conversation_and_clears_memory() -> None:
    """删除数据库会话成功后应清理对应 Redis 短期记忆。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    memory_manager = SimpleNamespace(delete_context=AsyncMock())
    service = build_service(db=db, memory_manager=memory_manager)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation),
        delete=AsyncMock(),
    )

    asyncio.run(service.delete(user_id=user_id, conversation_id=conversation.id))

    service.repository.delete.assert_awaited_once_with(conversation)
    db.commit.assert_awaited_once_with()
    memory_manager.delete_context.assert_awaited_once_with(str(conversation.id))


def test_cache_delete_failure_does_not_rollback_conversation_delete() -> None:
    """PostgreSQL 删除成功后 Redis 清理失败只能记录降级，不能恢复会话。"""
    user_id = uuid4()
    conversation = build_conversation(user_id=user_id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    memory_manager = SimpleNamespace(
        delete_context=AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    )
    service = build_service(db=db, memory_manager=memory_manager)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation),
        delete=AsyncMock(),
    )

    asyncio.run(service.delete(user_id=user_id, conversation_id=conversation.id))

    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [{}, {"title": None}, {"is_archived": None}],
)
def test_conversation_update_rejects_empty_or_null_changes(payload: dict) -> None:
    """PATCH 请求必须至少包含一个值有效的可更新字段。"""
    with pytest.raises(ValidationError):
        ConversationUpdate.model_validate(payload)


def test_create_route_uses_authenticated_user_instead_of_fixed_uuid() -> None:
    """创建路由必须把当前登录用户 ID 传给 Service。"""
    current_user = SimpleNamespace(id=uuid4())
    now = datetime.now(timezone.utc)
    expected = ConversationResponse(
        id=uuid4(),
        title="项目会议",
        model_name="mock",
        temperature=0.7,
        total_tokens=0,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    service = SimpleNamespace(create=AsyncMock(return_value=expected))
    request = ConversationCreate(title="项目会议", model_name="mock")

    response = asyncio.run(
        create_conversation(
            request=request,
            service=service,
            current_user=current_user,
        )
    )

    assert response == expected
    service.create.assert_awaited_once_with(
        user_id=current_user.id,
        conversation=request,
    )


def test_conversation_openapi_is_versioned_and_protected() -> None:
    """全部会话接口应位于 v1 路径，并声明 Bearer 安全要求。"""
    paths = create_app().openapi()["paths"]
    collection = paths["/api/v1/conversations"]
    detail = paths["/api/v1/conversations/{conversation_id}"]

    assert {"get", "post"}.issubset(collection)
    assert {"get", "patch", "delete"}.issubset(detail)
    assert collection["get"]["security"]
    assert detail["delete"]["security"]


def build_service(
    db=None,
    memory_manager=None,
) -> ConversationService:
    """构造绕过真实基础设施的会话 Service 测试对象。"""
    service = object.__new__(ConversationService)
    service.db = db or SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service.memory_manager = memory_manager or SimpleNamespace()
    return service


def build_conversation(user_id) -> ConversationEntity:
    """创建包含响应字段的会话测试实体。"""
    now = datetime.now(timezone.utc)
    return ConversationEntity(
        id=uuid4(),
        user_id=user_id,
        title="项目会议",
        system_prompt=None,
        model_name="mock",
        temperature=0.7,
        total_tokens=0,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
