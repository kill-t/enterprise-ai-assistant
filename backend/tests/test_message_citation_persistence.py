import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.infrastructure.llm.schemas import ChatResponse
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.repository.message_citation_repository import (
    MessageCitationRepository,
)
from app.modules.conversation.repository.message_repository import MessageRepository
from app.modules.conversation.service.conversation_service import ConversationService
from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchHit,
)
from app.modules.knowledge.source_locator import (
    build_legacy_source_locator,
    infer_source_type,
)


def test_citation_repository_replaces_snapshots_without_committing() -> None:
    """Repository 应删除旧引用、按 S1 顺序映射完整快照，但不能越权提交事务。"""
    db = SimpleNamespace(
        execute=AsyncMock(),
        add_all=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    repository = MessageCitationRepository(db)
    message_id = uuid4()
    first = build_source(score=0.93, content="第一条证据", page_start=2, page_end=3)
    second = build_source(score=0.81, content="第二条证据", page_start=None)

    entities = asyncio.run(
        repository.replace_for_message(
            message_id=message_id,
            sources=(first, second),
        )
    )

    delete_statement = db.execute.await_args.args[0]
    compiled = delete_statement.compile(dialect=postgresql.dialect())
    saved_entities = db.add_all.call_args.args[0]

    assert "DELETE FROM message_citations" in str(compiled)
    assert message_id in compiled.params.values()
    assert entities == saved_entities
    assert [entity.source_order for entity in entities] == [1, 2]
    assert all(entity.message_id == message_id for entity in entities)
    assert entities[0].knowledge_base_id == first.knowledge_base_id
    assert entities[0].document_id == first.document_id
    assert entities[0].chunk_id == first.chunk_id
    assert entities[0].knowledge_base_name_snapshot == first.knowledge_base_name
    assert entities[0].filename_snapshot == first.filename
    assert entities[0].chunk_index_snapshot == first.chunk_index
    assert entities[0].content_snapshot == "第一条证据"
    assert entities[0].heading_path_snapshot == first.heading_path
    assert entities[0].page_start == 2
    assert entities[0].page_end == 3
    assert entities[0].source_type_snapshot == first.source_type
    assert entities[0].source_locator_snapshot == first.source_locator.model_dump(
        mode="json"
    )
    assert entities[0].retrieval_score == 0.93
    db.flush.assert_awaited_once_with()
    db.commit.assert_not_awaited()


def test_citation_repository_empty_sources_clears_old_rows() -> None:
    """普通聊天、拒答或重试后无来源时，也要删除该消息可能残留的旧引用。"""
    db = SimpleNamespace(
        execute=AsyncMock(),
        add_all=Mock(),
        flush=AsyncMock(),
    )
    repository = MessageCitationRepository(db)

    entities = asyncio.run(
        repository.replace_for_message(message_id=uuid4(), sources=())
    )

    assert entities == []
    db.execute.assert_awaited_once()
    db.add_all.assert_not_called()
    db.flush.assert_awaited_once_with()


def test_message_repository_can_compensate_pending_finalization_failure() -> None:
    """回答与引用事务失败后，应通过条件 UPDATE 把原占位补偿为 FAILED。"""
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()))
    repository = MessageRepository(db)
    message_id = uuid4()
    finished_at = datetime.now(timezone.utc)

    updated = asyncio.run(
        repository.mark_failed_if_pending(
            message_id=message_id,
            error_code=ErrorCode.CITATION_PERSISTENCE_FAILED.value,
            error_message="回答引用保存失败，请重试",
            finished_at=finished_at,
        )
    )

    statement = db.scalar.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert updated is True
    assert "UPDATE messages" in sql
    assert "messages.status" in sql
    assert message_id in compiled.params.values()
    assert MessageStatus.PENDING in compiled.params.values()
    assert MessageStatus.FAILED in compiled.params.values()
    assert ErrorCode.CITATION_PERSISTENCE_FAILED.value in compiled.params.values()
    assert "回答引用保存失败，请重试" in compiled.params.values()
    assert finished_at in compiled.params.values()


def test_completed_answer_and_citations_flush_before_one_commit() -> None:
    """助手正文和最终来源必须在同一完成事务中依次 flush 后只提交一次。"""
    events: list[str] = []
    source = build_source(score=0.91, content="发布前必须完成安全评审")
    assistant_message = build_pending_assistant_message()
    service, memory_manager, summary_job_service = build_completion_service(
        events=events,
    )
    response = ChatResponse(
        content="发布前需要完成安全评审 [S1]。",
        model="mock-grounded",
        prompt_tokens=20,
        completion_tokens=8,
        total_tokens=28,
    )

    result = asyncio.run(
        service._complete_assistant_response(
            conversation=SimpleNamespace(id=assistant_message.conversation_id),
            conversation_id=assistant_message.conversation_id,
            assistant_message=assistant_message,
            assistant_response=response,
            sources=(source,),
        )
    )

    assert events == ["message_flush", "citation_flush", "commit"]
    service.citation_repository.replace_for_message.assert_awaited_once_with(
        message_id=assistant_message.id,
        sources=(source,),
    )
    assert result.status == MessageStatus.COMPLETED
    assert result.content == response.content
    assert [item.content for item in memory_manager.saved] == [response.content]
    summary_job_service.maybe_schedule.assert_awaited_once()


def test_citation_failure_rolls_back_answer_and_compensates_failed_terminal_state() -> None:
    """引用 flush 失败时不得提交回答，并应把已提交占位补偿为可重试失败状态。"""
    events: list[str] = []
    source = build_source(score=0.91, content="发布前必须完成安全评审")
    assistant_message = build_pending_assistant_message()
    service, memory_manager, summary_job_service = build_completion_service(
        events=events,
        citation_error=RuntimeError("citation insert failed"),
    )

    with pytest.raises(BusinessException) as error:
        asyncio.run(
            service._complete_assistant_response(
                conversation=SimpleNamespace(id=assistant_message.conversation_id),
                conversation_id=assistant_message.conversation_id,
                assistant_message=assistant_message,
                assistant_response=ChatResponse(
                    content="发布前需要完成安全评审 [S1]。",
                    model="mock-grounded",
                ),
                sources=(source,),
            )
        )

    assert error.value.code == ErrorCode.CITATION_PERSISTENCE_FAILED
    assert error.value.status_code == 500
    assert events == [
        "message_flush",
        "citation_flush_failed",
        "rollback",
        "mark_failed",
        "commit",
    ]
    service.message_repository.mark_failed_if_pending.assert_awaited_once_with(
        assistant_message.id,
        error_code=ErrorCode.CITATION_PERSISTENCE_FAILED.value,
        error_message="回答引用保存失败，请稍后重试",
        finished_at=assistant_message.finished_at,
    )
    assert assistant_message.status == MessageStatus.FAILED
    assert assistant_message.content == ""
    assert assistant_message.error_code == ErrorCode.CITATION_PERSISTENCE_FAILED.value
    assert memory_manager.saved == []
    summary_job_service.maybe_schedule.assert_not_awaited()


def build_completion_service(
    events: list[str],
    citation_error: Exception | None = None,
) -> tuple[ConversationService, SimpleNamespace, SimpleNamespace]:
    """构造可观察回答、引用、提交、回滚与外部副作用顺序的 Service。"""

    async def update_message(message: MessageEntity) -> MessageEntity:
        """记录助手消息 flush。"""
        events.append("message_flush")
        return message

    async def replace_citations(**kwargs):
        """记录引用 flush，并可稳定模拟数据库异常。"""
        if citation_error is not None:
            events.append("citation_flush_failed")
            raise citation_error
        events.append("citation_flush")
        return []

    async def mark_failed(*args, **kwargs) -> bool:
        """模拟补偿条件 UPDATE 命中原 PENDING 占位。"""
        events.append("mark_failed")
        return True

    async def commit() -> None:
        """记录当前事务提交。"""
        events.append("commit")

    async def rollback() -> None:
        """记录当前事务回滚。"""
        events.append("rollback")

    message_repository = SimpleNamespace(
        update=AsyncMock(side_effect=update_message),
        mark_failed_if_pending=AsyncMock(side_effect=mark_failed),
    )
    citation_repository = SimpleNamespace(
        replace_for_message=AsyncMock(side_effect=replace_citations)
    )
    memory_manager = SimpleNamespace(saved=[])

    async def save_message(conversation_id: str, message) -> None:
        """记录提交成功后的 Redis 回填。"""
        memory_manager.saved.append(message)

    memory_manager.save_message = save_message
    summary_job_service = SimpleNamespace(maybe_schedule=AsyncMock())
    service = object.__new__(ConversationService)
    service.db = SimpleNamespace(
        commit=AsyncMock(side_effect=commit),
        rollback=AsyncMock(side_effect=rollback),
    )
    service.message_repository = message_repository
    service.citation_repository = citation_repository
    service.memory_manager = memory_manager
    service.summary_job_service = summary_job_service
    return service, memory_manager, summary_job_service


def build_pending_assistant_message() -> MessageEntity:
    """构造已经提交到数据库的 PENDING 助手占位。"""
    now = datetime.now(timezone.utc)
    return MessageEntity(
        id=uuid4(),
        conversation_id=uuid4(),
        role=MessageRole.ASSISTANT,
        content="",
        model_name="mock",
        status=MessageStatus.PENDING,
        created_at=now,
    )


def build_source(
    score: float,
    content: str,
    page_start: int | None = 2,
    page_end: int | None = 2,
) -> MultiKnowledgeSearchHit:
    """构造模型实际看到的一条最终证据。"""
    return MultiKnowledgeSearchHit(
        knowledge_base_id=uuid4(),
        knowledge_base_name="项目资料",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project-guide.md",
        chunk_index=3,
        content=content,
        heading_path="项目 > 发布",
        page_start=page_start,
        page_end=page_end,
        token_count=20,
        score=score,
        source_type=infer_source_type("project-guide.md"),
        source_locator=build_legacy_source_locator(
            page_start=page_start,
            page_end=page_end,
            heading_path=("项目", "发布"),
        ),
    )
