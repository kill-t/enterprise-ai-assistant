import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.common.enums import SummaryJobStatus
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.conversation_summary_dispatch_service import (
    ConversationSummaryDispatchService,
)
from app.modules.conversation.service.conversation_summary_recovery_service import (
    ConversationSummaryRecoveryService,
)


class RecordingDatabase:
    """记录恢复和投递 Service 的事务提交与回滚。"""

    def __init__(self, events: list[str] | None = None) -> None:
        """保存共享事件列表。"""
        self.events = events if events is not None else []

    async def commit(self) -> None:
        """记录一次事务提交。"""
        self.events.append("commit")

    async def rollback(self) -> None:
        """记录一次事务回滚。"""
        self.events.append("rollback")


class RecordingDispatcher:
    """记录投递顺序，并允许模拟 Broker 故障。"""

    def __init__(
        self,
        events: list[str],
        error: Exception | None = None,
    ) -> None:
        """配置事件列表和可选异常。"""
        self.events = events
        self.error = error

    async def dispatch(self, job_id) -> str:
        """记录投递并返回稳定任务 ID。"""
        self.events.append("dispatch")
        if self.error is not None:
            raise self.error
        return str(job_id)


def test_repository_claims_dispatch_lease_with_due_and_expiry_conditions() -> None:
    """投递权必须同时检查 PENDING、到期时间和旧租约是否失效。"""
    claimed = SimpleNamespace(id=uuid4(), status=SummaryJobStatus.PENDING)
    db = SimpleNamespace(scalar=AsyncMock(return_value=claimed))
    repository = ConversationSummaryJobRepository(db)
    now = datetime.now(timezone.utc)

    result = asyncio.run(
        repository.claim_dispatch(
            job_id=claimed.id,
            claimed_at=now,
            lease_expired_before=now - timedelta(minutes=2),
        )
    )
    statement = str(db.scalar.await_args.args[0])

    assert result is claimed
    assert "conversation_summary_jobs.status" in statement
    assert "conversation_summary_jobs.next_retry_at" in statement
    assert "conversation_summary_jobs.dispatch_claimed_at" in statement


def test_repository_requeues_stale_running_jobs_atomically_in_bounded_batches() -> None:
    """卡死任务必须由一条限量 UPDATE 恢复，避免先查询后更新产生并发竞态。"""
    scalar_result = SimpleNamespace(all=Mock(return_value=[]))
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = ConversationSummaryJobRepository(db)
    now = datetime.now(timezone.utc)

    result = asyncio.run(
        repository.requeue_stale_running(
            stale_before=now - timedelta(minutes=10),
            failed_at=now,
            next_retry_at=now + timedelta(seconds=30),
            batch_size=50,
        )
    )
    statement = str(db.scalars.await_args.args[0])

    assert result == []
    assert statement.startswith("UPDATE conversation_summary_jobs")
    assert "conversation_summary_jobs.status" in statement
    assert "conversation_summary_jobs.started_at" in statement
    assert "conversation_summary_jobs.attempts" in statement
    assert "LIMIT" in statement


def test_dispatch_service_commits_claim_before_broker_publish() -> None:
    """发布前必须先提交数据库投递租约，防止两个扫描器同时发送。"""
    events: list[str] = []
    job_id = uuid4()
    repository = SimpleNamespace(
        claim_dispatch=AsyncMock(
            side_effect=lambda **kwargs: (
                events.append("claim") or SimpleNamespace(id=kwargs["job_id"])
            )
        ),
        mark_dispatched=AsyncMock(
            side_effect=lambda **kwargs: events.append("mark") or SimpleNamespace()
        ),
        release_dispatch_claim=AsyncMock(),
    )
    service = ConversationSummaryDispatchService(
        db=RecordingDatabase(events),
        job_repository=repository,
        dispatcher=RecordingDispatcher(events),
        dispatch_lease_seconds=120,
    )

    result = asyncio.run(service.dispatch_if_due(job_id))

    assert result == "dispatched"
    assert events == ["claim", "commit", "dispatch", "mark", "commit"]
    repository.release_dispatch_claim.assert_not_awaited()


def test_dispatch_service_releases_claim_when_broker_is_unavailable() -> None:
    """Broker 发布失败时必须释放投递租约，使后续扫描仍可接管。"""
    events: list[str] = []
    job_id = uuid4()
    repository = SimpleNamespace(
        claim_dispatch=AsyncMock(return_value=SimpleNamespace(id=job_id)),
        mark_dispatched=AsyncMock(),
        release_dispatch_claim=AsyncMock(return_value=SimpleNamespace()),
    )
    service = ConversationSummaryDispatchService(
        db=RecordingDatabase(events),
        job_repository=repository,
        dispatcher=RecordingDispatcher(
            events,
            error=ConnectionError("Redis Broker 不可用"),
        ),
        dispatch_lease_seconds=120,
    )

    result = asyncio.run(service.dispatch_if_due(job_id))

    assert result == "publish_failed"
    repository.mark_dispatched.assert_not_awaited()
    repository.release_dispatch_claim.assert_awaited_once()
    assert events == ["commit", "dispatch", "commit"]


def test_recovery_requeues_stale_running_and_dispatches_due_pending() -> None:
    """恢复扫描应处理卡死任务、耗尽任务，并把到期 PENDING 交给投递服务。"""
    now = datetime.now(timezone.utc)
    due_job_ids = [uuid4(), uuid4()]
    repository = SimpleNamespace(
        fail_exhausted_stale_running=AsyncMock(return_value=[SimpleNamespace()]),
        requeue_stale_running=AsyncMock(
            return_value=[SimpleNamespace(), SimpleNamespace()]
        ),
        list_due_dispatch_ids=AsyncMock(return_value=due_job_ids),
    )
    dispatch_service = SimpleNamespace(
        dispatch_if_due=AsyncMock(side_effect=["dispatched", "skipped"])
    )
    db = RecordingDatabase()
    service = ConversationSummaryRecoveryService(
        db=db,
        job_repository=repository,
        dispatch_service=dispatch_service,
        running_timeout_seconds=600,
        stale_retry_delay_seconds=30,
        batch_size=100,
    )

    result = asyncio.run(service.recover(now=now))

    assert result.failed_exhausted == 1
    assert result.requeued_stale == 2
    assert result.dispatch_candidates == 2
    assert result.dispatched == 1
    requeue_args = repository.requeue_stale_running.await_args.kwargs
    assert requeue_args["stale_before"] == now - timedelta(seconds=600)
    assert requeue_args["next_retry_at"] == now + timedelta(seconds=30)
    assert db.events == ["commit", "commit"]


def test_celery_registers_periodic_summary_recovery_task() -> None:
    """Celery Worker 和 Beat 必须发现恢复任务及其周期配置。"""
    celery_app.loader.import_default_modules()

    assert "conversation.recover_summary_jobs" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["recover-conversation-summary-jobs"]
    assert schedule["task"] == "conversation.recover_summary_jobs"
    assert schedule["schedule"] > 0
