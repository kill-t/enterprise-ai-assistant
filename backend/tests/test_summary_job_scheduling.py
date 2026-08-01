import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.common.enums import MessageRole, SummaryJobStatus
from app.infrastructure.task_queue.summary_job_dispatcher import (
    CelerySummaryJobDispatcher,
)
from app.modules.conversation.service.conversation_service import ConversationService
from app.modules.conversation.service.conversation_summary_job_service import (
    ConversationSummaryJobService,
)


class CharacterMessageCounter:
    """按消息正文字符数提供稳定的摘要触发测试预算。"""

    def count_messages(self, messages) -> int:
        """返回全部消息正文的字符数。"""
        return sum(len(message.content) for message in messages)


class RecordingDatabase:
    """记录事务提交和回滚顺序的异步数据库替身。"""

    def __init__(self, events: list[str]) -> None:
        """保存共享事件列表。"""
        self.events = events

    async def commit(self) -> None:
        """记录事务提交。"""
        self.events.append("commit")

    async def rollback(self) -> None:
        """记录事务回滚。"""
        self.events.append("rollback")


class RecordingDispatcher:
    """记录 Celery 投递，并可模拟 Broker 不可用。"""

    def __init__(
        self,
        events: list[str],
        error: Exception | None = None,
    ) -> None:
        """配置事件列表和可选投递异常。"""
        self.events = events
        self.error = error
        self.job_ids = []

    async def dispatch(self, job_id) -> str:
        """记录投递；配置异常时模拟 Redis Broker 故障。"""
        self.events.append("dispatch")
        self.job_ids.append(job_id)
        if self.error is not None:
            raise self.error
        return str(job_id)


def build_message(
    role: MessageRole,
    content: str,
    created_at: datetime | None = None,
):
    """创建摘要触发测试使用的已完成消息替身。"""
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        content=content,
        created_at=created_at or datetime.now(timezone.utc),
    )


def build_service(
    messages,
    *,
    active_job=None,
    threshold: int = 10,
    recent_budget: int = 4,
    batch_budget: int = 100,
    dispatcher=None,
    events: list[str] | None = None,
):
    """组装可观察的摘要任务 Service 和依赖替身。"""
    event_log = events if events is not None else []
    db = RecordingDatabase(event_log)
    job_repository = SimpleNamespace(
        get_active_by_conversation_id=AsyncMock(return_value=active_job),
        create=AsyncMock(side_effect=lambda job: job),
        update=AsyncMock(side_effect=lambda job: job),
        claim_dispatch=AsyncMock(
            side_effect=lambda job_id, **kwargs: SimpleNamespace(id=job_id)
        ),
        mark_dispatched=AsyncMock(return_value=SimpleNamespace()),
        release_dispatch_claim=AsyncMock(return_value=SimpleNamespace()),
    )
    service = ConversationSummaryJobService(
        db=db,
        message_repository=SimpleNamespace(
            list_completed_by_conversation=AsyncMock(return_value=messages),
            list_completed_after_cursor=AsyncMock(return_value=messages),
        ),
        summary_repository=SimpleNamespace(
            get_by_conversation_id=AsyncMock(return_value=None)
        ),
        job_repository=job_repository,
        dispatcher=dispatcher or RecordingDispatcher(event_log),
        summary_trigger_tokens=threshold,
        recent_history_budget_tokens=recent_budget,
        summary_batch_tokens=batch_budget,
        dispatch_lease_seconds=120,
        token_counter_factory=lambda model_name: CharacterMessageCounter(),
    )
    return service, db, job_repository, event_log


def test_history_below_threshold_does_not_create_or_dispatch_job() -> None:
    """未摘要历史未超过阈值时，不应制造无意义任务。"""
    messages = [
        build_message(MessageRole.USER, "问"),
        build_message(MessageRole.ASSISTANT, "答"),
    ]
    service, _, job_repository, events = build_service(messages, threshold=2)

    job = asyncio.run(
        service.maybe_schedule(
            SimpleNamespace(id=uuid4(), model_name="mock")
        )
    )

    assert job is None
    job_repository.create.assert_not_awaited()
    assert events == []


def test_job_is_committed_before_it_is_dispatched() -> None:
    """必须先让 PostgreSQL 任务可见，再把 job ID 交给 Worker。"""
    events: list[str] = []
    dispatcher = RecordingDispatcher(events)
    messages = [
        build_message(MessageRole.USER, "准备项目会议"),
        build_message(MessageRole.ASSISTANT, "已经整理会议资料"),
    ]
    service, _, job_repository, _ = build_service(
        messages,
        threshold=5,
        dispatcher=dispatcher,
        events=events,
    )
    conversation = SimpleNamespace(id=uuid4(), model_name="mock")

    job = asyncio.run(service.maybe_schedule(conversation))

    assert job is not None
    assert job.status == SummaryJobStatus.PENDING
    assert job.target_message_id == messages[-1].id
    assert job.idempotency_key == (
        f"conversation-summary:{conversation.id}:{messages[-1].id}"
    )
    assert events == ["commit", "commit", "dispatch", "commit"]
    job_repository.create.assert_awaited_once_with(job)
    job_repository.claim_dispatch.assert_awaited_once()
    job_repository.mark_dispatched.assert_awaited_once()


def test_dispatch_failure_keeps_committed_pending_job() -> None:
    """Broker 故障不能删除数据库任务，也不能让摘要失败影响聊天。"""
    events: list[str] = []
    dispatcher = RecordingDispatcher(events, ConnectionError("Redis 不可用"))
    messages = [
        build_message(MessageRole.USER, "较长用户问题"),
        build_message(MessageRole.ASSISTANT, "较长助手回答"),
    ]
    service, _, job_repository, _ = build_service(
        messages,
        threshold=5,
        dispatcher=dispatcher,
        events=events,
    )

    job = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert job is not None
    assert job.status == SummaryJobStatus.PENDING
    assert job.celery_task_id is None
    assert events == ["commit", "commit", "dispatch", "commit"]
    job_repository.mark_dispatched.assert_not_awaited()
    job_repository.release_dispatch_claim.assert_awaited_once()


def test_existing_undispatched_pending_job_is_dispatched_again() -> None:
    """发现没有 celery_task_id 的 PENDING 任务时，应重新尝试投递而非新建任务。"""
    events: list[str] = []
    active_job = SimpleNamespace(
        id=uuid4(),
        status=SummaryJobStatus.PENDING,
        celery_task_id=None,
    )
    dispatcher = RecordingDispatcher(events)
    service, _, job_repository, _ = build_service(
        [],
        active_job=active_job,
        dispatcher=dispatcher,
        events=events,
    )

    result = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert result is active_job
    assert events == ["commit", "dispatch", "commit"]
    job_repository.create.assert_not_awaited()
    job_repository.mark_dispatched.assert_awaited_once()


def test_pending_retry_is_not_dispatched_before_next_retry_time() -> None:
    """尚未到期的 PENDING 重试任务不能被新聊天请求提前投递。"""
    events: list[str] = []
    active_job = SimpleNamespace(
        id=uuid4(),
        status=SummaryJobStatus.PENDING,
        celery_task_id=None,
        next_retry_at=datetime.now(timezone.utc).replace(year=2099),
    )
    service, _, job_repository, _ = build_service(
        [],
        active_job=active_job,
        events=events,
    )

    result = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert result is active_job
    assert events == []
    job_repository.claim_dispatch.assert_not_awaited()


def test_existing_dispatched_or_running_job_is_not_dispatched_twice() -> None:
    """已经投递或运行中的活动任务不应因新消息再次投递。"""
    for active_job in (
        SimpleNamespace(
            id=uuid4(),
            status=SummaryJobStatus.PENDING,
            celery_task_id="already-dispatched",
        ),
        SimpleNamespace(
            id=uuid4(),
            status=SummaryJobStatus.RUNNING,
            celery_task_id="running-task",
        ),
    ):
        events: list[str] = []
        service, _, job_repository, _ = build_service(
            [], active_job=active_job, events=events
        )

        result = asyncio.run(
            service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
        )

        assert result is active_job
        assert events == []
        job_repository.create.assert_not_awaited()


def test_only_messages_through_latest_assistant_are_frozen() -> None:
    """尾部尚未回答的用户消息不能成为摘要任务目标。"""
    assistant = build_message(MessageRole.ASSISTANT, "完整回答")
    messages = [
        build_message(MessageRole.USER, "完整问题"),
        assistant,
        build_message(MessageRole.USER, "尚未回答的问题"),
    ]
    service, _, _, _ = build_service(messages, threshold=1)

    job = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert job is not None
    assert job.target_message_id == assistant.id


def test_summary_target_leaves_recent_complete_turns_uncovered() -> None:
    """摘要任务只冻结旧前缀，最近完整轮次应继续以原文进入后续上下文。"""
    messages = [
        build_message(MessageRole.USER, "问一"),
        build_message(MessageRole.ASSISTANT, "答一"),
        build_message(MessageRole.USER, "问二"),
        build_message(MessageRole.ASSISTANT, "答二"),
        build_message(MessageRole.USER, "问三"),
        build_message(MessageRole.ASSISTANT, "答三"),
    ]
    service, _, _, _ = build_service(
        messages,
        threshold=8,
        recent_budget=4,
    )

    job = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert job is not None
    assert job.target_message_id == messages[3].id


def test_summary_target_limits_old_history_to_one_token_batch() -> None:
    """积压旧历史超过批次预算时，本次任务只能冻结最早的一批完整轮次。"""
    messages = [
        build_message(MessageRole.USER, "问一"),
        build_message(MessageRole.ASSISTANT, "答一"),
        build_message(MessageRole.USER, "问二"),
        build_message(MessageRole.ASSISTANT, "答二"),
        build_message(MessageRole.USER, "问三"),
        build_message(MessageRole.ASSISTANT, "答三"),
        build_message(MessageRole.USER, "问四"),
        build_message(MessageRole.ASSISTANT, "答四"),
    ]
    service, _, _, _ = build_service(
        messages,
        threshold=8,
        recent_budget=4,
        batch_budget=8,
    )

    job = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert job is not None
    assert job.target_message_id == messages[3].id


def test_one_oversized_old_turn_is_kept_whole_to_let_cursor_progress() -> None:
    """单轮超过批次软上限时宁可完整处理一轮，也不能截断或永远卡住游标。"""
    messages = [
        build_message(MessageRole.USER, "超长问题"),
        build_message(MessageRole.ASSISTANT, "完整回答"),
        build_message(MessageRole.USER, "新问"),
        build_message(MessageRole.ASSISTANT, "新答"),
    ]
    service, _, _, _ = build_service(
        messages,
        threshold=5,
        recent_budget=4,
        batch_budget=3,
    )

    job = asyncio.run(
        service.maybe_schedule(SimpleNamespace(id=uuid4(), model_name="mock"))
    )

    assert job is not None
    assert job.target_message_id == messages[1].id


def test_celery_dispatcher_sends_only_job_id_with_deterministic_task_id() -> None:
    """Broker 消息只允许携带 job ID，并使用相同 ID 关联 Celery 日志。"""
    async_result = SimpleNamespace(id="celery-result-id")
    celery = SimpleNamespace(send_task=Mock(return_value=async_result))
    dispatcher = CelerySummaryJobDispatcher(celery)
    job_id = uuid4()

    task_id = asyncio.run(dispatcher.dispatch(job_id))

    assert task_id == "celery-result-id"
    celery.send_task.assert_called_once_with(
        "conversation.process_summary_job",
        args=[str(job_id)],
        task_id=str(job_id),
        queue="conversation-summary",
        retry=False,
    )


def test_conversation_service_swallows_summary_scheduling_failure() -> None:
    """任务调度内部错误不得覆盖已经提交的助手回复。"""
    db = SimpleNamespace(rollback=AsyncMock())
    conversation_service = object.__new__(ConversationService)
    conversation_service.db = db
    conversation_service.summary_job_service = SimpleNamespace(
        maybe_schedule=AsyncMock(side_effect=RuntimeError("任务数据库异常"))
    )

    asyncio.run(
        conversation_service._schedule_summary_safely(
            SimpleNamespace(id=uuid4(), model_name="mock")
        )
    )

    db.rollback.assert_awaited_once()
