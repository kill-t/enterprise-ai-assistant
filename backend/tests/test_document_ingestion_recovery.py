import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.common.enums import DocumentIngestionJobStatus
from app.core.config import WorkerSettings
from app.infrastructure.task_queue.celery_app import celery_app
from app.infrastructure.task_queue.document_ingestion_dispatcher import (
    CeleryDocumentIngestionDispatcher,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.service.document_ingestion_dispatch_service import (
    DocumentIngestionDispatchService,
)
from app.modules.knowledge.service.document_ingestion_recovery_service import (
    DocumentIngestionRecoveryService,
)


class RecordingDatabase:
    """记录恢复与投递服务的事务操作顺序。"""

    def __init__(self, events: list[str] | None = None) -> None:
        """保存共享事件列表。"""
        self.events = events if events is not None else []

    async def commit(self) -> None:
        """记录一次提交。"""
        self.events.append("commit")

    async def rollback(self) -> None:
        """记录一次回滚。"""
        self.events.append("rollback")


class RecordingDispatcher:
    """记录 Broker 发布，并可模拟 Redis 不可用。"""

    def __init__(
        self,
        events: list[str],
        error: Exception | None = None,
    ) -> None:
        """保存事件列表和可选发布异常。"""
        self.events = events
        self.error = error

    async def dispatch(self, job_id) -> str:
        """记录发布顺序，并返回与任务关联的 Celery ID。"""
        self.events.append("dispatch")
        if self.error is not None:
            raise self.error
        return str(job_id)


def test_repository_claims_document_dispatch_lease_with_due_conditions() -> None:
    """投递权必须检查 PENDING、重试到期、剩余次数和旧租约过期。"""
    claimed = SimpleNamespace(
        id=uuid4(),
        status=DocumentIngestionJobStatus.PENDING,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=claimed))
    repository = DocumentIngestionJobRepository(db)
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
    assert "document_ingestion_jobs.status" in statement
    assert "document_ingestion_jobs.next_retry_at" in statement
    assert "document_ingestion_jobs.dispatch_claimed_at" in statement
    assert "document_ingestion_jobs.attempts" in statement


def test_repository_requeues_stale_document_jobs_in_bounded_atomic_update() -> None:
    """卡死任务恢复必须通过带 LIMIT 的单条 UPDATE，避免查询后并发抢改。"""
    scalar_result = SimpleNamespace(all=Mock(return_value=[]))
    db = SimpleNamespace(scalars=AsyncMock(return_value=scalar_result))
    repository = DocumentIngestionJobRepository(db)
    now = datetime.now(timezone.utc)

    result = asyncio.run(
        repository.requeue_stale_running(
            stale_before=now - timedelta(minutes=15),
            failed_at=now,
            next_retry_at=now + timedelta(seconds=30),
            batch_size=50,
        )
    )
    statement = str(db.scalars.await_args.args[0])

    assert result == []
    assert statement.startswith("UPDATE document_ingestion_jobs")
    assert "document_ingestion_jobs.started_at" in statement
    assert "document_ingestion_jobs.attempts" in statement
    assert "LIMIT" in statement


def test_worker_settings_protect_document_recovery_time_boundaries() -> None:
    """恢复阈值须晚于硬超时，投递租约也不能短于扫描周期。"""
    with pytest.raises(ValueError, match="文档运行超时恢复阈值"):
        WorkerSettings(document_running_timeout_seconds=300)
    with pytest.raises(ValueError, match="文档投递租约"):
        WorkerSettings(
            document_recovery_interval_seconds=121,
            document_dispatch_lease_seconds=120,
        )


def test_document_dispatch_commits_lease_before_broker_publish() -> None:
    """发布前先提交 PostgreSQL 租约，才能阻止两个发布者同时发送。"""
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
    service = DocumentIngestionDispatchService(
        db=RecordingDatabase(events),
        job_repository=repository,
        dispatcher=RecordingDispatcher(events),
        dispatch_lease_seconds=120,
    )

    result = asyncio.run(service.dispatch_if_due(job_id))

    assert result == "dispatched"
    assert events == ["claim", "commit", "dispatch", "mark", "commit"]
    repository.release_dispatch_claim.assert_not_awaited()


def test_document_dispatch_releases_lease_when_broker_fails() -> None:
    """Broker 发布失败必须释放租约，下一轮扫描才能立即重新接管。"""
    events: list[str] = []
    job_id = uuid4()
    repository = SimpleNamespace(
        claim_dispatch=AsyncMock(return_value=SimpleNamespace(id=job_id)),
        mark_dispatched=AsyncMock(),
        release_dispatch_claim=AsyncMock(return_value=SimpleNamespace()),
    )
    service = DocumentIngestionDispatchService(
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


def test_celery_document_dispatcher_sends_only_job_id_to_dedicated_queue() -> None:
    """Broker 消息只携带 job ID，并进入独立文档入库队列。"""
    celery = SimpleNamespace(
        send_task=Mock(return_value=SimpleNamespace(id="celery-result-id"))
    )
    dispatcher = CeleryDocumentIngestionDispatcher(celery)
    job_id = uuid4()

    task_id = asyncio.run(dispatcher.dispatch(job_id))

    assert task_id == "celery-result-id"
    celery.send_task.assert_called_once_with(
        "knowledge.process_document_ingestion_job",
        args=[str(job_id)],
        task_id=str(job_id),
        queue="document-ingestion",
        retry=False,
    )


def test_recovery_synchronizes_stale_jobs_documents_and_due_dispatch() -> None:
    """恢复扫描应同步文档状态，再将到期 PENDING 交给投递服务。"""
    now = datetime.now(timezone.utc)
    failed_jobs = [SimpleNamespace(document_id=uuid4())]
    requeued_jobs = [
        SimpleNamespace(document_id=uuid4()),
        SimpleNamespace(document_id=uuid4()),
    ]
    due_job_ids = [uuid4(), uuid4()]
    job_repository = SimpleNamespace(
        fail_exhausted_stale_running=AsyncMock(return_value=failed_jobs),
        requeue_stale_running=AsyncMock(return_value=requeued_jobs),
        list_due_dispatch_ids=AsyncMock(return_value=due_job_ids),
    )
    document_repository = SimpleNamespace(
        mark_recovery_failed=AsyncMock(),
        mark_recovery_pending=AsyncMock(),
    )
    dispatch_service = SimpleNamespace(
        dispatch_if_due=AsyncMock(side_effect=["dispatched", "skipped"])
    )
    db = RecordingDatabase()
    service = DocumentIngestionRecoveryService(
        db=db,
        job_repository=job_repository,
        document_repository=document_repository,
        dispatch_service=dispatch_service,
        running_timeout_seconds=900,
        stale_retry_delay_seconds=30,
        batch_size=100,
    )

    result = asyncio.run(service.recover(now=now))

    assert result.failed_exhausted == 1
    assert result.requeued_stale == 2
    assert result.dispatch_candidates == 2
    assert result.dispatched == 1
    requeue_args = job_repository.requeue_stale_running.await_args.kwargs
    assert requeue_args["stale_before"] == now - timedelta(seconds=900)
    assert requeue_args["next_retry_at"] == now + timedelta(seconds=30)
    document_repository.mark_recovery_failed.assert_awaited_once_with(
        document_ids=[failed_jobs[0].document_id],
        failed_at=now,
        error_code="DOCUMENT_WORKER_LOST_MAX_ATTEMPTS",
        error_message="文档入库进程中断且已达到最大次数",
    )
    document_repository.mark_recovery_pending.assert_awaited_once()
    assert db.events == ["commit", "commit"]


def test_celery_registers_periodic_document_recovery_task() -> None:
    """Celery Worker、路由和 Beat 必须发现文档恢复任务。"""
    celery_app.loader.import_default_modules()

    assert "knowledge.recover_document_ingestion_jobs" in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["recover-document-ingestion-jobs"]
    assert schedule["task"] == "knowledge.recover_document_ingestion_jobs"
    assert schedule["schedule"] > 0
    assert schedule["options"]["queue"] == "document-ingestion"
