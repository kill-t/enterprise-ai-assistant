import asyncio
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.error_code import ErrorCode
from app.infrastructure.llm.context_manager import ContextManager
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.infrastructure.llm.schemas import (
    ChatResponse,
    ChatStreamCompletedEvent,
    ChatStreamDeltaEvent,
    ChatStreamErrorEvent,
)
from app.modules.conversation.schema.message_schema import (
    MessageStreamCompletedEvent,
    MessageStreamDeltaEvent,
    MessageStreamErrorEvent,
    MessageStreamStartedEvent,
)
from app.modules.conversation.service.conversation_service import ConversationService


class RecordingDatabase:
    """记录流式聊天事务顺序的异步数据库替身。"""

    def __init__(self, events: list[str]) -> None:
        """保存共享事件列表。"""
        self.events = events

    async def commit(self) -> None:
        """记录事务提交。"""
        self.events.append("commit")

    async def rollback(self) -> None:
        """记录事务回滚。"""
        self.events.append("rollback")


class RecordingMemoryManager:
    """返回空历史并记录成功写入 Redis 的消息角色。"""

    def __init__(self, events: list[str]) -> None:
        """保存共享事件列表。"""
        self.events = events

    async def get_context(self, conversation_id: str):
        """返回空缓存，让测试从空历史开始。"""
        return []

    async def save_message(self, conversation_id: str, message) -> None:
        """记录缓存写入，但不依赖真实 Redis。"""
        self.events.append(f"memory:{message.role}")


class ScriptedStreamProvider:
    """按给定顺序产生 Provider 事件或抛出异常。"""

    def __init__(self, events) -> None:
        """保存本次测试需要播放的事件脚本。"""
        self.events = events
        self.started = False
        self.closed = False

    async def stream_chat(self, request):
        """逐项播放脚本，Exception 项用于模拟不遵守契约的 Provider。"""
        self.started = True
        try:
            for event in self.events:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.closed = True


def build_service(provider, event_log: list[str] | None = None):
    """组装可观察事务、持久化、缓存和摘要调度的会话服务。"""
    events = event_log if event_log is not None else []
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id,
        system_prompt=None,
        temperature=0.7,
        model_name="mock",
    )

    def persist_message(message):
        """模拟数据库生成消息 ID 和创建时间。"""
        message.id = message.id or uuid4()
        message.created_at = message.created_at or datetime.now(timezone.utc)
        events.append(f"create:{message.role.value}")
        return message

    def update_message(message):
        """记录终态更新，便于确认 delta 期间没有写数据库。"""
        events.append(f"update:{message.status.value}")
        return message

    message_repository = SimpleNamespace(
        create=AsyncMock(side_effect=persist_message),
        update=AsyncMock(side_effect=update_message),
        list_completed_by_conversation=AsyncMock(return_value=[]),
    )
    summary_job_service = SimpleNamespace(
        maybe_schedule=AsyncMock(
            side_effect=lambda _: events.append("schedule-summary")
        )
    )
    service = object.__new__(ConversationService)
    service.db = RecordingDatabase(events)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=conversation)
    )
    service.message_repository = message_repository
    service.summary_repository = SimpleNamespace(
        get_by_conversation_id=AsyncMock(return_value=None)
    )
    service.summary_job_service = summary_job_service
    service.memory_manager = RecordingMemoryManager(events)
    service.provider = provider
    service.prompt_builder = PromptBuilder()
    service.context_manager = ContextManager()
    service.history_mapper = HistoryMapper()
    return (
        service,
        conversation_id,
        message_repository,
        summary_job_service,
        events,
    )


async def collect_stream(service, conversation_id):
    """消费 Service 的完整内部流，供同步测试断言。"""
    return [
        event
        async for event in service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
    ]


def test_conversation_stream_message_is_an_async_generator() -> None:
    """Service 流必须能被后续 SSE 层使用 async for 消费。"""
    assert inspect.isasyncgenfunction(ConversationService.stream_message)


def test_stream_creates_message_pair_before_started_and_writes_only_at_end() -> None:
    """用户和助手占位应先原子提交，delta 期间不更新消息表。"""
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="会议"),
            ChatStreamDeltaEvent(delta="完成"),
            ChatStreamCompletedEvent(
                response=ChatResponse(
                    content="会议完成",
                    model="mock-stream",
                    prompt_tokens=10,
                    completion_tokens=2,
                    total_tokens=12,
                )
            ),
        ]
    )
    service, conversation_id, repository, summary_jobs, events = build_service(provider)

    async def scenario():
        """逐个读取事件，观察第一个 delta 到达时的数据库状态。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
        started = await anext(stream)
        assert events == [
            "create:user",
            "create:assistant",
            "commit",
            "memory:user",
        ]

        first_delta = await anext(stream)
        assert repository.update.await_count == 0
        remaining = [event async for event in stream]
        assert provider.closed is True
        return started, first_delta, remaining

    started, first_delta, remaining = asyncio.run(scenario())
    completed = remaining[-1]

    assert isinstance(started, MessageStreamStartedEvent)
    assert started.user_message.status == MessageStatus.COMPLETED
    assert started.assistant_message.status == MessageStatus.PENDING
    assert isinstance(first_delta, MessageStreamDeltaEvent)
    assert first_delta.delta == "会议"
    assert first_delta.message_id == started.assistant_message.id
    assert isinstance(completed, MessageStreamCompletedEvent)
    assert completed.message.content == "会议完成"
    assert completed.message.model_name == "mock-stream"
    assert repository.update.await_count == 1
    assert events[-4:] == [
        "update:completed",
        "commit",
        "memory:assistant",
        "schedule-summary",
    ]
    summary_jobs.maybe_schedule.assert_awaited_once()


def test_stream_error_persists_partial_text_as_failed_without_caching_it() -> None:
    """部分输出后失败时只在终点写一次 FAILED，且失败回答不进入 Redis。"""
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="部分回答"),
            ChatStreamErrorEvent(
                error_code="LLM_STREAM_ERROR",
                error_message="模型流式调用失败",
                model="mock-stream",
            ),
        ]
    )
    service, conversation_id, repository, summary_jobs, events = build_service(provider)

    stream_events = asyncio.run(collect_stream(service, conversation_id))
    failed = stream_events[-1]

    assert isinstance(failed, MessageStreamErrorEvent)
    assert failed.message.status == MessageStatus.FAILED
    assert failed.message.content == "部分回答"
    assert failed.message.error_code == ErrorCode.MODEL_REQUEST_FAILED.value
    assert repository.update.await_count == 1
    assert "memory:assistant" not in events
    summary_jobs.maybe_schedule.assert_not_awaited()


def test_stream_without_terminal_event_is_protocol_failure() -> None:
    """Provider 静默结束时不能把半截正文当成成功回答。"""
    provider = ScriptedStreamProvider([ChatStreamDeltaEvent(delta="未完成")])
    service, conversation_id, _, summary_jobs, _ = build_service(provider)

    stream_events = asyncio.run(collect_stream(service, conversation_id))
    failed = stream_events[-1]

    assert isinstance(failed, MessageStreamErrorEvent)
    assert failed.message.status == MessageStatus.FAILED
    assert failed.message.content == "未完成"
    assert failed.message.error_code == ErrorCode.MODEL_STREAM_PROTOCOL_ERROR.value
    summary_jobs.maybe_schedule.assert_not_awaited()


def test_stream_rejects_completed_content_that_differs_from_deltas() -> None:
    """completed 完整正文与已发送 delta 不一致时应记录协议错误。"""
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="用户已经看到的正文"),
            ChatStreamCompletedEvent(
                response=ChatResponse(content="另一段正文", model="mock-stream")
            ),
        ]
    )
    service, conversation_id, _, _, _ = build_service(provider)

    stream_events = asyncio.run(collect_stream(service, conversation_id))
    failed = stream_events[-1]

    assert isinstance(failed, MessageStreamErrorEvent)
    assert failed.message.error_code == ErrorCode.MODEL_STREAM_PROTOCOL_ERROR.value
    assert failed.message.content == "用户已经看到的正文"


def test_stream_maps_unexpected_provider_timeout_to_failed_event() -> None:
    """即使 Provider 直接抛异常，Service 也必须保存失败状态并结束事件流。"""
    provider = ScriptedStreamProvider([TimeoutError("模型连接超时")])
    service, conversation_id, _, _, _ = build_service(provider)

    stream_events = asyncio.run(collect_stream(service, conversation_id))
    failed = stream_events[-1]

    assert isinstance(failed, MessageStreamErrorEvent)
    assert failed.message.error_code == ErrorCode.MODEL_TIMEOUT.value
    assert failed.message.error_message == "模型响应超时，请稍后重试"


def test_message_pair_creation_failure_rolls_back_before_provider_starts() -> None:
    """助手占位创建失败时必须回滚用户消息，且不能开始模型调用。"""
    provider = ScriptedStreamProvider([])
    service, conversation_id, repository, _, events = build_service(provider)

    def fail_when_creating_assistant(message):
        """让用户消息写入成功，但在同一事务的助手占位写入阶段失败。"""
        events.append(f"create:{message.role.value}")
        if message.role == MessageRole.ASSISTANT:
            raise RuntimeError("助手消息写入失败")
        message.id = uuid4()
        message.created_at = datetime.now(timezone.utc)
        return message

    repository.create.side_effect = fail_when_creating_assistant

    async def scenario() -> None:
        """尝试读取第一个事件，从而真正执行异步生成器准备阶段。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
        await anext(stream)

    with pytest.raises(RuntimeError, match="助手消息写入失败"):
        asyncio.run(scenario())

    assert events[-1] == "rollback"
    assert "commit" not in events
    assert provider.started is False
