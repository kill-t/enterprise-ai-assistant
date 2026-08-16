import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.common.enums import MessageRole, MessageStatus, SourceType
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.main import create_app
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.entity.message_citation import MessageCitationEntity
from app.modules.conversation.repository.message_citation_repository import (
    MessageCitationRepository,
)
from app.modules.conversation.schema.message_schema import MessageHistoryResponse
from app.modules.conversation.service.conversation_service import ConversationService


def test_citation_repository_reads_many_messages_in_one_ordered_query() -> None:
    """历史引用必须通过一次 IN 查询读取，并按消息与 S1/S2 顺序稳定排序。"""
    first_message_id = uuid4()
    second_message_id = uuid4()
    expected = [
        build_citation(message_id=first_message_id, source_order=1),
        build_citation(message_id=second_message_id, source_order=1),
    ]
    scalar_result = SimpleNamespace(all=Mock(return_value=expected))
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = MessageCitationRepository(db)

    entities = asyncio.run(
        repository.list_by_message_ids((first_message_id, second_message_id))
    )

    statement = db.scalars.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert entities == expected
    assert "message_citations.message_id IN" in sql
    assert "ORDER BY message_citations.message_id" in sql
    assert "message_citations.source_order" in sql
    assert set(compiled.params["message_id_1"]) == {
        first_message_id,
        second_message_id,
    }
    scalar_result.all.assert_called_once_with()


def test_citation_repository_skips_database_for_empty_message_ids() -> None:
    """空消息列表不应生成无意义或方言不一致的 IN 空集合查询。"""
    db = SimpleNamespace(scalars=AsyncMock())
    repository = MessageCitationRepository(db)

    entities = asyncio.run(repository.list_by_message_ids(()))

    assert entities == []
    db.scalars.assert_not_awaited()


def test_history_groups_snapshot_citations_without_n_plus_one_queries() -> None:
    """历史消息应批量分组引用，并把数据库快照名映射成稳定 API 字段。"""
    user_id = uuid4()
    conversation_id = uuid4()
    user_message = build_message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="发布前要做什么？",
    )
    assistant_message = build_message(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="需要完成安全评审 [S1]，并确认负责人 [S2]。",
    )
    second = build_citation(
        message_id=assistant_message.id,
        source_order=2,
        filename="owners.md",
        content="评审负责人需要在任务系统确认。",
        score=0.82,
    )
    first = build_citation(
        message_id=assistant_message.id,
        source_order=1,
        filename="release-guide.pdf",
        content="发布前必须完成安全评审。",
        score=0.93,
    )
    service = build_history_service(
        conversation_id=conversation_id,
        user_id=user_id,
        messages=[user_message, assistant_message],
        # 故意返回乱序数据，Service 仍应按 source_order 输出 S1、S2。
        citations=[second, first],
    )

    responses = asyncio.run(
        service.get_messages(user_id=user_id, conversation_id=conversation_id)
    )

    assert all(isinstance(item, MessageHistoryResponse) for item in responses)
    assert responses[0].citations == []
    assert [item.source_order for item in responses[1].citations] == [1, 2]
    assert [item.filename for item in responses[1].citations] == [
        "release-guide.pdf",
        "owners.md",
    ]
    assert responses[1].citations[0].knowledge_base_name == "项目资料"
    assert responses[1].citations[0].content == "发布前必须完成安全评审。"
    assert responses[1].citations[0].score == 0.93
    assert responses[1].citations[0].source_type == SourceType.PDF
    assert responses[1].citations[0].source_locator.kind == "page"
    service.message_repository.list_by_conversation.assert_awaited_once_with(
        conversation_id
    )
    service.citation_repository.list_by_message_ids.assert_awaited_once_with(
        (user_message.id, assistant_message.id)
    )


def test_history_ownership_failure_does_not_query_messages_or_citations() -> None:
    """跨用户访问统一返回 404，并且所有权校验失败后不能继续读取任何历史数据。"""
    service = object.__new__(ConversationService)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=None)
    )
    service.message_repository = SimpleNamespace(
        list_by_conversation=AsyncMock()
    )
    service.citation_repository = SimpleNamespace(
        list_by_message_ids=AsyncMock()
    )

    with pytest.raises(BusinessException) as error:
        asyncio.run(
            service.get_messages(
                user_id=uuid4(),
                conversation_id=uuid4(),
            )
        )

    assert error.value.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert error.value.status_code == 404
    service.message_repository.list_by_conversation.assert_not_awaited()
    service.citation_repository.list_by_message_ids.assert_not_awaited()


def test_history_openapi_exposes_public_fields_instead_of_snapshot_columns() -> None:
    """OpenAPI 应描述稳定业务字段，不泄露数据库快照列的命名细节。"""
    schemas = create_app().openapi()["components"]["schemas"]
    properties = schemas["MessageCitationResponse"]["properties"]

    assert {"knowledge_base_name", "filename", "content", "score"}.issubset(
        properties
    )
    assert {
        "knowledge_base_name_snapshot",
        "filename_snapshot",
        "content_snapshot",
        "retrieval_score",
    }.isdisjoint(properties)


def build_history_service(
    *,
    conversation_id,
    user_id,
    messages: list[MessageEntity],
    citations: list[MessageCitationEntity],
) -> ConversationService:
    """构造只包含所有权、消息和引用仓储的历史读取 Service。"""
    service = object.__new__(ConversationService)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(
            return_value=SimpleNamespace(id=conversation_id, user_id=user_id)
        )
    )
    service.message_repository = SimpleNamespace(
        list_by_conversation=AsyncMock(return_value=messages)
    )
    service.citation_repository = SimpleNamespace(
        list_by_message_ids=AsyncMock(return_value=citations)
    )
    return service


def build_message(
    *,
    conversation_id,
    role: MessageRole,
    content: str,
) -> MessageEntity:
    """构造带完整响应字段的历史消息实体。"""
    now = datetime.now(timezone.utc)
    return MessageEntity(
        id=uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        model_name="mock" if role == MessageRole.ASSISTANT else "user",
        status=MessageStatus.COMPLETED,
        created_at=now,
        finished_at=now,
    )


def build_citation(
    *,
    message_id,
    source_order: int,
    filename: str = "release-guide.pdf",
    content: str = "发布前必须完成安全评审。",
    score: float = 0.9,
) -> MessageCitationEntity:
    """构造包含活外键和不可变快照的引用实体。"""
    return MessageCitationEntity(
        id=uuid4(),
        message_id=message_id,
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        chunk_id=uuid4(),
        knowledge_base_name_snapshot="项目资料",
        filename_snapshot=filename,
        chunk_index_snapshot=source_order - 1,
        content_snapshot=content,
        heading_path_snapshot="项目 > 发布",
        page_start=2,
        page_end=2,
        source_type_snapshot=(
            SourceType.PDF if filename.lower().endswith(".pdf") else SourceType.MARKDOWN
        ),
        source_locator_snapshot={
            "kind": "page",
            "page_start": 2,
            "page_end": 2,
            "heading_path": ["项目", "发布"],
        },
        source_order=source_order,
        retrieval_score=score,
        created_at=datetime.now(timezone.utc),
    )
