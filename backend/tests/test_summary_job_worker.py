import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.common.enums import SummaryJobStatus
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.conversation_summary_worker_service import (
    ConversationSummaryWorkerService,
)
from app.modules.conversation.service.summary_failure_policy import (
    SummaryFailurePolicy,
)


class SuccessfulProcessor:
    """记录任务并模拟真正摘要处理成功。"""

    def __init__(self) -> None:
        """初始化处理记录。"""
        self.jobs = []
        self.persisted_results = []

    async def prepare(self, job):
        """记录任务并模拟数据库读取阶段。"""
        self.jobs.append(job)
        return SimpleNamespace(job=job)

    async def generate(self, prepared):
        """模拟不占数据库事务的模型生成阶段。"""
        return SimpleNamespace(content="摘要结果")

    async def persist(self, prepared, generated) -> None:
        """记录等待最终事务提交的摘要结果。"""
        self.persisted_results.append((prepared, generated))


class FailingProcessor:
    """模拟摘要处理逻辑失败。"""

    async def prepare(self, job):
        """模拟数据库读取成功。"""
        return SimpleNamespace(job=job)

    async def generate(self, prepared):
        """在模型生成阶段抛出固定异常。"""
        raise RuntimeError("模拟摘要生成失败")

    async def persist(self, prepared, generated) -> None:
        """生成失败后不应调用持久化阶段。"""
        raise AssertionError("生成失败后不应持久化")


class TimeoutProcessor(FailingProcessor):
    """模拟可通过稍后重试恢复的模型超时。"""

    async def generate(self, prepared):
        """抛出内置超时异常，触发延迟重试策略。"""
        raise TimeoutError("模拟模型超时")


class RecordingDatabase:
    """记录 Worker 事务结束次数。"""

    def __init__(self) -> None:
        """初始化提交和回滚计数。"""
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        """记录一次提交。"""
        self.commits += 1

    async def rollback(self) -> None:
        """记录一次回滚。"""
        self.rollbacks += 1


def build_claimed_job(attempts: int = 1, max_attempts: int = 3):
    """创建已经原子领取为 RUNNING 的任务替身。"""
    return SimpleNamespace(
        id=uuid4(),
        status=SummaryJobStatus.RUNNING,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def test_repository_claim_is_single_atomic_conditional_update() -> None:
    """任务领取必须通过一个带状态和次数条件的 UPDATE 完成。"""
    claimed_job = build_claimed_job()
    db = SimpleNamespace(scalar=AsyncMock(return_value=claimed_job))
    repository = ConversationSummaryJobRepository(db)
    job_id = claimed_job.id

    result = asyncio.run(
        repository.claim_pending(job_id, datetime.now(timezone.utc))
    )
    statement = str(db.scalar.await_args.args[0])

    assert result is claimed_job
    assert statement.startswith("UPDATE conversation_summary_jobs")
    assert "conversation_summary_jobs.id" in statement
    assert "conversation_summary_jobs.status" in statement
    assert "conversation_summary_jobs.attempts < conversation_summary_jobs.max_attempts" in statement
    assert "conversation_summary_jobs.next_retry_at" in statement
    assert "attempts=(conversation_summary_jobs.attempts +" in statement


def test_repository_retry_returns_running_job_to_due_pending() -> None:
    """可重试失败必须按 attempts 原子恢复 PENDING，并保存下一次执行时间。"""
    pending_job = SimpleNamespace(status=SummaryJobStatus.PENDING)
    db = SimpleNamespace(scalar=AsyncMock(return_value=pending_job))
    repository = ConversationSummaryJobRepository(db)
    now = datetime.now(timezone.utc)

    result = asyncio.run(
        repository.mark_retry_pending(
            job_id=uuid4(),
            expected_attempts=1,
            failed_at=now,
            next_retry_at=now,
            error_code="SUMMARY_PROVIDER_TIMEOUT",
            error_message="摘要模型调用超时",
        )
    )
    statement = str(db.scalar.await_args.args[0])

    assert result is pending_job
    assert "conversation_summary_jobs.status" in statement
    assert "conversation_summary_jobs.attempts" in statement
    assert "conversation_summary_jobs.next_retry_at" in statement


def test_repository_completion_checks_attempt_version() -> None:
    """任务收尾必须同时匹配 RUNNING 和 attempts，阻止迟到 Worker 覆盖新重试。"""
    completed_job = SimpleNamespace(status=SummaryJobStatus.SUCCEEDED)
    db = SimpleNamespace(scalar=AsyncMock(return_value=completed_job))
    repository = ConversationSummaryJobRepository(db)

    result = asyncio.run(
        repository.mark_succeeded(
            job_id=uuid4(),
            expected_attempts=2,
            finished_at=datetime.now(timezone.utc),
        )
    )
    statement = str(db.scalar.await_args.args[0])

    assert result is completed_job
    assert "conversation_summary_jobs.status" in statement
    assert "conversation_summary_jobs.attempts" in statement


def test_duplicate_or_finished_job_is_skipped_without_processing() -> None:
    """原子领取失败表示任务已被其他 Worker 领取或已结束，应直接跳过。"""
    db = RecordingDatabase()
    repository = SimpleNamespace(claim_pending=AsyncMock(return_value=None))
    processor = SuccessfulProcessor()
    service = ConversationSummaryWorkerService(db, repository, processor)

    result = asyncio.run(service.execute(uuid4()))

    assert result == "skipped"
    assert processor.jobs == []
    assert db.commits == 1


def test_claimed_job_is_processed_and_marked_succeeded() -> None:
    """成功处理必须在领取提交后执行，并按相同 attempts 完成任务。"""
    job = build_claimed_job(attempts=2)
    db = RecordingDatabase()
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=job),
        mark_succeeded=AsyncMock(
            return_value=SimpleNamespace(status=SummaryJobStatus.SUCCEEDED)
        ),
        mark_failed=AsyncMock(),
    )
    processor = SuccessfulProcessor()
    service = ConversationSummaryWorkerService(db, repository, processor)

    result = asyncio.run(service.execute(job.id))

    assert result == "succeeded"
    assert processor.jobs == [job]
    repository.mark_succeeded.assert_awaited_once()
    assert repository.mark_succeeded.await_args.kwargs["expected_attempts"] == 2
    repository.mark_failed.assert_not_awaited()
    assert len(processor.persisted_results) == 1
    assert db.commits == 3


def test_processor_failure_is_persisted_without_leaking_raw_error() -> None:
    """处理异常应先回滚半成品，再保存稳定错误信息并结束本次消息。"""
    job = build_claimed_job()
    db = RecordingDatabase()
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=job),
        mark_succeeded=AsyncMock(),
        mark_failed=AsyncMock(
            return_value=SimpleNamespace(status=SummaryJobStatus.FAILED)
        ),
    )
    service = ConversationSummaryWorkerService(db, repository, FailingProcessor())

    result = asyncio.run(service.execute(job.id))

    assert result == "failed"
    repository.mark_succeeded.assert_not_awaited()
    repository.mark_failed.assert_awaited_once()
    failure_args = repository.mark_failed.await_args.kwargs
    assert failure_args["expected_attempts"] == 1
    assert failure_args["error_code"] == "SUMMARY_UNEXPECTED_ERROR"
    assert failure_args["error_message"] == "会话摘要任务处理失败"
    assert "模拟摘要生成失败" not in failure_args["error_message"]
    assert db.commits == 3
    assert db.rollbacks == 1


def test_retryable_timeout_is_scheduled_with_backoff() -> None:
    """模型超时且次数未耗尽时应回到 PENDING，而不是直接永久失败。"""
    job = build_claimed_job(attempts=1, max_attempts=3)
    db = RecordingDatabase()
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=job),
        mark_succeeded=AsyncMock(),
        mark_failed=AsyncMock(),
        mark_retry_pending=AsyncMock(
            return_value=SimpleNamespace(status=SummaryJobStatus.PENDING)
        ),
    )
    service = ConversationSummaryWorkerService(
        db,
        repository,
        TimeoutProcessor(),
        failure_policy=SummaryFailurePolicy(
            base_delay_seconds=30,
            max_delay_seconds=300,
        ),
    )

    result = asyncio.run(service.execute(job.id))

    assert result == "retry_scheduled"
    repository.mark_failed.assert_not_awaited()
    repository.mark_retry_pending.assert_awaited_once()
    retry_args = repository.mark_retry_pending.await_args.kwargs
    assert retry_args["error_code"] == "SUMMARY_PROVIDER_TIMEOUT"
    assert retry_args["error_message"] == "摘要模型调用超时"
    assert retry_args["next_retry_at"] > retry_args["failed_at"]


def test_retryable_failure_becomes_terminal_after_max_attempts() -> None:
    """最后一次执行仍超时时必须进入 FAILED，不能无限重试。"""
    job = build_claimed_job(attempts=3, max_attempts=3)
    db = RecordingDatabase()
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=job),
        mark_succeeded=AsyncMock(),
        mark_retry_pending=AsyncMock(),
        mark_failed=AsyncMock(
            return_value=SimpleNamespace(status=SummaryJobStatus.FAILED)
        ),
    )
    service = ConversationSummaryWorkerService(
        db,
        repository,
        TimeoutProcessor(),
    )

    result = asyncio.run(service.execute(job.id))

    assert result == "failed"
    repository.mark_retry_pending.assert_not_awaited()
    repository.mark_failed.assert_awaited_once()


def test_stale_worker_cannot_report_success_for_newer_attempt() -> None:
    """attempts 不匹配时必须回滚旧 Worker 产生的摘要半成品。"""
    job = build_claimed_job(attempts=1)
    db = RecordingDatabase()
    repository = SimpleNamespace(
        claim_pending=AsyncMock(return_value=job),
        mark_succeeded=AsyncMock(return_value=None),
        mark_failed=AsyncMock(),
    )
    service = ConversationSummaryWorkerService(
        db,
        repository,
        SuccessfulProcessor(),
    )

    result = asyncio.run(service.execute(job.id))

    assert result == "stale"
    assert db.commits == 2
    assert db.rollbacks == 1


def test_celery_app_registers_summary_job_task() -> None:
    """Worker 启动时必须发现 Dispatcher 使用的同名摘要任务。"""
    celery_app.loader.import_default_modules()

    assert "conversation.process_summary_job" in celery_app.tasks
