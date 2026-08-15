import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
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
from app.modules.conversation.schema.conversation_knowledge_search_schema import (
    ConversationKnowledgeSearchResponse,
)
from app.modules.conversation.schema.message_schema import (
    MessageStreamCompletedEvent,
    MessageStreamDeltaEvent,
    MessageStreamErrorEvent,
    MessageStreamSourcesEvent,
    MessageStreamStartedEvent,
)
from app.modules.conversation.service.conversation_service import ConversationService
from app.modules.conversation.service.grounded_prompt_builder import (
    GroundedPromptBuilder,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchHit,
)


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
        self.requests = []
        self.started = False
        self.closed = False

    async def stream_chat(self, request):
        """逐项播放脚本，Exception 项用于模拟不遵守契约的 Provider。"""
        self.requests.append(request)
        self.started = True
        try:
            for event in self.events:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            self.closed = True


class BlockingStreamProvider:
    """产生一个片段后保持等待，供测试真实任务取消。"""

    def __init__(self) -> None:
        """创建可观察的等待和关闭状态。"""
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def stream_chat(self, request):
        """发送一个片段，然后阻塞到测试取消消费任务。"""
        try:
            yield ChatStreamDeltaEvent(delta="部分回答")
            self.waiting.set()
            await self.release.wait()
        finally:
            self.closed = True


def build_service(
    provider,
    event_log: list[str] | None = None,
    knowledge_search_result: ConversationKnowledgeSearchResponse | None = None,
):
    """组装可观察事务、持久化、缓存和摘要调度的会话服务。"""
    events = event_log if event_log is not None else []
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id,
        system_prompt=None,
        temperature=0.7,
        model_name="mock",
    )
    created_messages = {}

    def persist_message(message):
        """模拟数据库生成消息 ID 和创建时间。"""
        message.id = message.id or uuid4()
        message.created_at = message.created_at or datetime.now(timezone.utc)
        created_messages[message.id] = message
        events.append(f"create:{message.role.value}")
        return message

    def update_message(message):
        """记录终态更新，便于确认 delta 期间没有写数据库。"""
        events.append(f"update:{message.status.value}")
        return message

    def cancel_pending_message(
        message_id,
        *,
        partial_content,
        error_code,
        error_message,
        finished_at,
    ):
        """模拟只允许 PENDING 原子进入 CANCELLED 的仓储更新。"""
        message = created_messages[message_id]
        if message.status != MessageStatus.PENDING:
            events.append("cancel:skipped")
            return False
        message.content = partial_content
        message.status = MessageStatus.CANCELLED
        message.error_code = error_code
        message.error_message = error_message
        message.finished_at = finished_at
        events.append("cancel:cancelled")
        return True

    message_repository = SimpleNamespace(
        create=AsyncMock(side_effect=persist_message),
        update=AsyncMock(side_effect=update_message),
        mark_cancelled_if_pending=AsyncMock(side_effect=cancel_pending_message),
        list_completed_by_conversation=AsyncMock(return_value=[]),
        entities=created_messages,
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
    service.citation_repository = SimpleNamespace(
        replace_for_message=AsyncMock(return_value=[])
    )
    service.summary_repository = SimpleNamespace(
        get_by_conversation_id=AsyncMock(return_value=None)
    )
    service.summary_job_service = summary_job_service
    service.memory_manager = RecordingMemoryManager(events)
    service.provider = provider
    service.prompt_builder = PromptBuilder()
    service.grounded_prompt_builder = GroundedPromptBuilder()
    service.context_manager = ContextManager()
    service.history_mapper = HistoryMapper()
    service.knowledge_search_service = SimpleNamespace(
        search=AsyncMock(
            return_value=knowledge_search_result
            or build_knowledge_search_result(
                conversation_id=conversation_id,
                searched_knowledge_base_ids=[],
                results=[],
            )
        )
    )
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


def test_grounded_stream_emits_sources_before_delta_and_persists_same_sources() -> None:
    """流式 RAG 必须先给出模型实际看到的 S1/S2，再输出正文并保存同一组引用。"""
    knowledge_base_id = uuid4()
    first_source = build_search_hit(
        knowledge_base_id=knowledge_base_id,
        score=0.93,
        content="发布前必须完成安全评审。",
    )
    second_source = build_search_hit(
        knowledge_base_id=knowledge_base_id,
        score=0.82,
        content="评审负责人需要在任务系统确认。",
    )
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="发布前需要完成安全评审 [S1]。"),
            ChatStreamCompletedEvent(
                response=ChatResponse(
                    content="发布前需要完成安全评审 [S1]。",
                    model="mock-stream-grounded",
                )
            ),
        ]
    )
    search_result = build_knowledge_search_result(
        conversation_id=uuid4(),
        searched_knowledge_base_ids=[knowledge_base_id],
        results=[first_source, second_source],
    )
    service, conversation_id, _, _, _ = build_service(
        provider,
        knowledge_search_result=search_result,
    )
    # 让检索结果中的会话 ID 与测试 Service 动态生成的会话保持一致。
    service.knowledge_search_service.search.return_value = search_result.model_copy(
        update={"conversation_id": conversation_id}
    )

    stream_events = asyncio.run(collect_stream(service, conversation_id))

    assert [type(event) for event in stream_events] == [
        MessageStreamStartedEvent,
        MessageStreamSourcesEvent,
        MessageStreamDeltaEvent,
        MessageStreamCompletedEvent,
    ]
    sources_event = stream_events[1]
    assert [item.source_order for item in sources_event.sources] == [1, 2]
    assert [item.content for item in sources_event.sources] == [
        first_source.content,
        second_source.content,
    ]
    assert sources_event.message_id == stream_events[0].assistant_message.id
    assert "[S1]" in provider.requests[0].messages[-1].content
    service.citation_repository.replace_for_message.assert_awaited_once_with(
        message_id=stream_events[-1].message.id,
        sources=(first_source, second_source),
    )


def test_grounded_stream_without_evidence_completes_refusal_without_provider() -> None:
    """活动知识库没有可靠证据时应先发空 sources，再完成正常拒答且不启动 Provider。"""
    knowledge_base_id = uuid4()
    provider = ScriptedStreamProvider([])
    search_result = build_knowledge_search_result(
        conversation_id=uuid4(),
        searched_knowledge_base_ids=[knowledge_base_id],
        results=[],
    )
    service, conversation_id, _, _, _ = build_service(
        provider,
        knowledge_search_result=search_result,
    )
    service.knowledge_search_service.search.return_value = search_result.model_copy(
        update={"conversation_id": conversation_id}
    )

    stream_events = asyncio.run(collect_stream(service, conversation_id))

    assert [type(event) for event in stream_events] == [
        MessageStreamStartedEvent,
        MessageStreamSourcesEvent,
        MessageStreamCompletedEvent,
    ]
    assert stream_events[1].sources == []
    assert stream_events[-1].message.status == MessageStatus.COMPLETED
    assert stream_events[-1].message.model_name == "grounded-refusal"
    assert "没有在当前会话已选择的知识库中找到足够可靠的证据" in stream_events[-1].message.content
    assert provider.started is False
    service.citation_repository.replace_for_message.assert_awaited_once_with(
        message_id=stream_events[-1].message.id,
        sources=(),
    )


def test_stream_search_failure_happens_before_started_and_message_creation() -> None:
    """Embedding 或检索失败必须在 started 前返回，不能留下用户消息或助手占位。"""
    provider = ScriptedStreamProvider([])
    service, conversation_id, repository, _, _ = build_service(provider)
    service.knowledge_search_service.search.side_effect = BusinessException(
        code=ErrorCode.KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE,
        message="Embedding 服务暂时不可用",
        status_code=503,
    )

    async def scenario() -> None:
        """读取首个内部事件，触发流式请求的检索准备阶段。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="发布前要做什么？",
        )
        await anext(stream)

    with pytest.raises(BusinessException) as error:
        asyncio.run(scenario())

    assert error.value.code == ErrorCode.KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE
    assert error.value.status_code == 503
    repository.create.assert_not_awaited()
    assert provider.started is False


def build_knowledge_search_result(
    *,
    conversation_id,
    searched_knowledge_base_ids,
    results,
) -> ConversationKnowledgeSearchResponse:
    """构造流式 RAG 准备阶段使用的会话知识检索结果。"""
    return ConversationKnowledgeSearchResponse(
        conversation_id=conversation_id,
        query="发布前要做什么？",
        searched_knowledge_base_ids=searched_knowledge_base_ids,
        skipped_archived_knowledge_base_ids=[],
        similarity_threshold=0.2,
        results=results,
    )


def build_search_hit(
    *,
    knowledge_base_id,
    score: float,
    content: str,
) -> MultiKnowledgeSearchHit:
    """构造模型和 sources 事件必须共享的一条最终证据。"""
    return MultiKnowledgeSearchHit(
        knowledge_base_id=knowledge_base_id,
        knowledge_base_name="项目资料",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="release-guide.pdf",
        chunk_index=0,
        content=content,
        heading_path="项目 > 发布",
        page_start=2,
        page_end=2,
        token_count=20,
        score=score,
    )


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


def test_stream_marks_length_limited_response_as_retryable_failure() -> None:
    """模型达到输出上限时必须保留半截正文和用量，但绝不能标记为成功。"""
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="只生成了一半"),
            ChatStreamCompletedEvent(
                response=ChatResponse(
                    content="只生成了一半",
                    model="deepseek-v4-flash",
                    prompt_tokens=100,
                    completion_tokens=1024,
                    total_tokens=1124,
                    finish_reason="length",
                )
            ),
        ]
    )
    service, conversation_id, repository, summary_jobs, events = build_service(provider)

    stream_events = asyncio.run(collect_stream(service, conversation_id))
    failed = stream_events[-1]

    assert isinstance(failed, MessageStreamErrorEvent)
    assert failed.message.status == MessageStatus.FAILED
    assert failed.message.content == "只生成了一半"
    assert failed.message.model_name == "deepseek-v4-flash"
    persisted_message = repository.entities[failed.message.id]
    assert persisted_message.completion_tokens == 1024
    assert failed.message.error_code == ErrorCode.MODEL_OUTPUT_TRUNCATED.value
    assert failed.message.error_message == "模型回答达到长度上限，请重试"
    assert "memory:assistant" not in events
    summary_jobs.maybe_schedule.assert_not_awaited()


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


def test_closing_stream_after_started_marks_empty_assistant_cancelled() -> None:
    """客户端在首帧后断开时，应取消占位消息且不启动 Provider。"""
    provider = ScriptedStreamProvider([])
    service, conversation_id, repository, summary_jobs, events = build_service(provider)

    async def scenario():
        """读取 started 后立即关闭 Service 异步生成器。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
        started = await anext(stream)
        await stream.aclose()
        return started

    started = asyncio.run(scenario())
    assistant = repository.entities[started.assistant_message.id]

    assert assistant.status == MessageStatus.CANCELLED
    assert assistant.content == ""
    assert assistant.error_code == ErrorCode.GENERATION_CANCELLED.value
    assert assistant.error_message == "回答生成已取消"
    assert assistant.finished_at is not None
    assert provider.started is False
    assert events[-2:] == ["cancel:cancelled", "commit"]
    summary_jobs.maybe_schedule.assert_not_awaited()


def test_closing_stream_after_delta_persists_partial_text_as_cancelled() -> None:
    """断开发生在部分输出后时，应保存已发送正文并关闭 Provider。"""
    provider = ScriptedStreamProvider([ChatStreamDeltaEvent(delta="部分回答")])
    service, conversation_id, repository, _, events = build_service(provider)

    async def scenario():
        """读取 started 和首个 delta 后主动关闭业务流。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
        started = await anext(stream)
        delta = await anext(stream)
        await stream.aclose()
        return started, delta

    started, delta = asyncio.run(scenario())
    assistant = repository.entities[started.assistant_message.id]

    assert delta.delta == "部分回答"
    assert assistant.status == MessageStatus.CANCELLED
    assert assistant.content == "部分回答"
    assert provider.closed is True
    assert events[-2:] == ["cancel:cancelled", "commit"]


def test_task_cancellation_is_re_raised_after_persisting_cancelled_state() -> None:
    """运行中的请求任务被取消时，必须保存状态并继续传播 CancelledError。"""
    provider = BlockingStreamProvider()
    service, conversation_id, repository, _, _ = build_service(provider)
    observed_events = []

    async def scenario():
        """启动流消费任务，等待 Provider 阻塞后取消该任务。"""

        async def consume() -> None:
            """持续消费事件，直到外层任务发出取消。"""
            async for event in service.stream_message(
                user_id=uuid4(),
                conversation_id=conversation_id,
                content="整理会议纪要",
            ):
                observed_events.append(event)

        task = asyncio.create_task(consume())
        await provider.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    started = observed_events[0]
    assistant = repository.entities[started.assistant_message.id]

    assert assistant.status == MessageStatus.CANCELLED
    assert assistant.content == "部分回答"
    assert provider.closed is True


def test_cancellation_persistence_failure_rolls_back_without_swallowing_cancel() -> (
    None
):
    """取消终态写库失败时应回滚，但仍要把原始 CancelledError 传给上层。"""
    provider = BlockingStreamProvider()
    service, conversation_id, repository, _, events = build_service(provider)
    repository.mark_cancelled_if_pending.side_effect = RuntimeError("数据库写入失败")

    async def scenario() -> None:
        """取消正在等待 Provider 的消费任务，并观察清理失败后的控制流。"""

        async def consume() -> None:
            """消费消息流直到测试发出任务取消。"""
            async for _ in service.stream_message(
                user_id=uuid4(),
                conversation_id=conversation_id,
                content="整理会议纪要",
            ):
                pass

        task = asyncio.create_task(consume())
        await provider.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert provider.closed is True
    assert events[-1] == "rollback"


def test_closing_after_completed_does_not_overwrite_terminal_state() -> None:
    """客户端收到 completed 时断开，原子取消不能把成功消息改写。"""
    provider = ScriptedStreamProvider(
        [
            ChatStreamDeltaEvent(delta="完整回答"),
            ChatStreamCompletedEvent(
                response=ChatResponse(content="完整回答", model="mock-stream")
            ),
        ]
    )
    service, conversation_id, repository, _, events = build_service(provider)

    async def scenario():
        """停在 completed 事件处关闭生成器，模拟终点附近的竞争。"""
        stream = service.stream_message(
            user_id=uuid4(),
            conversation_id=conversation_id,
            content="整理会议纪要",
        )
        started = await anext(stream)
        await anext(stream)
        completed = await anext(stream)
        await stream.aclose()
        return started, completed

    started, completed = asyncio.run(scenario())
    assistant = repository.entities[started.assistant_message.id]

    assert completed.message.status == MessageStatus.COMPLETED
    assert assistant.status == MessageStatus.COMPLETED
    assert events[-2:] == ["cancel:skipped", "commit"]


def test_cancelled_message_status_migration_follows_current_head() -> None:
    """迁移必须从当前 head 增加 CANCELLED，并提供可执行的降级策略。"""
    migration = Path(
        "migrations/versions/b9d5e7f2a410_add_cancelled_message_status.py"
    ).read_text(encoding="utf-8")

    assert (
        'down_revision: Union[str, Sequence[str], None] = "a8c4e6f1b209"' in migration
    )
    assert "ALTER TYPE messagestatus ADD VALUE IF NOT EXISTS 'CANCELLED'" in migration
    assert "WHEN status::text = 'CANCELLED' THEN 'FAILED'" in migration
