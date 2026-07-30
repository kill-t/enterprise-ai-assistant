import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.common.enums import MessageRole, MessageStatus
from app.infrastructure.llm.context_manager import ContextManager
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.infrastructure.llm.schemas import ChatMessage, ChatRole
from app.modules.conversation.repository.message_repository import MessageRepository
from app.modules.conversation.service.conversation_service import ConversationService


class CharacterTokenCounter:
    """使用正文字符数提供可预测的上下文预算。"""

    def count_messages(self, messages: list[ChatMessage]) -> int:
        """返回所有消息正文的字符数总和。"""
        return sum(len(message.content) for message in messages)


def chat_message(role: ChatRole, content: str) -> ChatMessage:
    """简化标准聊天消息创建。"""
    return ChatMessage(role=role, content=content)


def build_message(
    role: MessageRole,
    content: str,
    created_at: datetime,
) -> SimpleNamespace:
    """创建已完成数据库消息，供摘要游标测试使用。"""
    return SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        role=role,
        content=content,
        model_name="mock",
        status=MessageStatus.COMPLETED,
        created_at=created_at,
        finished_at=created_at,
    )


def test_prompt_places_summary_between_system_prompt_and_recent_history() -> None:
    """最终请求顺序必须是系统提示词、摘要、近期历史和当前问题。"""
    builder = PromptBuilder()
    summary_message = builder.build_summary_message("## 用户目标与背景\n准备会议")

    request = builder.build(
        system_prompt="你是企业助手",
        summary_message=summary_message,
        history=[
            chat_message(ChatRole.USER, "最近问题"),
            chat_message(ChatRole.ASSISTANT, "最近回答"),
        ],
        user_message="当前问题",
    )

    assert [message.role for message in request.messages] == [
        ChatRole.SYSTEM,
        ChatRole.SYSTEM,
        ChatRole.USER,
        ChatRole.ASSISTANT,
        ChatRole.USER,
    ]
    assert "仅作为背景事实" in request.messages[1].content
    assert "<conversation_summary>" in request.messages[1].content


def test_summary_tokens_are_reserved_before_selecting_recent_turns() -> None:
    """摘要占用输入预算后，只能在剩余空间内选择最近完整轮次。"""
    manager = ContextManager(
        context_window_tokens=30,
        max_output_tokens=5,
        token_safety_margin=5,
        token_counter_factory=lambda model_name: CharacterTokenCounter(),
    )
    history = [
        chat_message(ChatRole.USER, "旧问题"),
        chat_message(ChatRole.ASSISTANT, "旧回答"),
        chat_message(ChatRole.USER, "新问"),
        chat_message(ChatRole.ASSISTANT, "新答"),
    ]

    selected = manager.build_history(
        history=history,
        system_prompt="系统",
        summary_message=chat_message(ChatRole.SYSTEM, "摘要内容一"),
        user_message="当前问题",
        model_name="mock",
    )

    assert [message.content for message in selected] == ["新问", "新答"]


def test_message_repository_queries_completed_messages_after_summary_cursor() -> None:
    """游标查询必须同时限制会话、完成状态、创建时间和消息 ID。"""
    scalar_result = SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = MessageRepository(db)
    conversation_id = uuid4()
    cursor_message_id = uuid4()
    cursor_created_at = datetime.now(timezone.utc)

    result = asyncio.run(
        repository.list_completed_after_cursor(
            conversation_id=conversation_id,
            covered_until_created_at=cursor_created_at,
            covered_until_message_id=cursor_message_id,
        )
    )
    statement = str(db.scalars.await_args.args[0])

    assert result == []
    assert "messages.conversation_id" in statement
    assert "messages.status" in statement
    assert "messages.created_at" in statement
    assert "messages.id" in statement


def test_service_uses_database_cursor_when_summary_exists() -> None:
    """存在摘要时必须读取游标后完整历史，不能仅依赖 Redis 最近 20 条。"""
    now = datetime.now(timezone.utc)
    recent_messages = [
        build_message(MessageRole.USER, "摘要之后的问题", now + timedelta(seconds=1)),
        build_message(
            MessageRole.ASSISTANT,
            "摘要之后的回答",
            now + timedelta(seconds=2),
        ),
    ]
    summary = SimpleNamespace(
        covered_until_created_at=now,
        covered_until_message_id=uuid4(),
        version=1,
    )
    service = object.__new__(ConversationService)
    service.memory_manager = SimpleNamespace(
        get_context=AsyncMock(side_effect=AssertionError("不应读取 Redis"))
    )
    service.message_repository = SimpleNamespace(
        list_completed_after_cursor=AsyncMock(return_value=recent_messages)
    )
    service.history_mapper = HistoryMapper()

    history = asyncio.run(service._get_history(uuid4(), summary=summary))

    assert [message.content for message in history] == [
        "摘要之后的问题",
        "摘要之后的回答",
    ]
    service.message_repository.list_completed_after_cursor.assert_awaited_once()
