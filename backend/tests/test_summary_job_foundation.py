import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.common.enums import SummaryJobStatus
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.summary_job_state_machine import (
    SummaryJobStateMachine,
)


def build_job(
    status: SummaryJobStatus = SummaryJobStatus.PENDING,
) -> ConversationSummaryJobEntity:
    """创建冻结目标游标的摘要任务实体。"""
    now = datetime.now(timezone.utc)
    return ConversationSummaryJobEntity(
        id=uuid4(),
        conversation_id=uuid4(),
        target_message_id=uuid4(),
        target_created_at=now,
        idempotency_key=f"summary:{uuid4()}",
        status=status,
        attempts=0,
        max_attempts=3,
        created_at=now,
        updated_at=now,
    )


def test_summary_job_freezes_target_and_has_database_constraints() -> None:
    """任务表必须保存目标边界，并约束尝试次数和幂等键。"""
    table = ConversationSummaryJobEntity.__table__

    assert table.c.target_message_id.nullable is True
    assert table.c.target_created_at.nullable is False
    assert table.c.idempotency_key.nullable is False
    assert any(
        constraint.name == "uq_conversation_summary_jobs_idempotency_key"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_conversation_summary_jobs_attempts_nonnegative"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_conversation_summary_jobs_max_attempts_positive"
        for constraint in table.constraints
    )


def test_only_one_active_summary_job_is_allowed_per_conversation() -> None:
    """部分唯一索引应阻止同一会话同时出现多个待处理或运行中任务。"""
    active_index = next(
        index
        for index in ConversationSummaryJobEntity.__table__.indexes
        if index.name == "uq_conversation_summary_jobs_active_conversation"
    )

    assert active_index.unique is True
    assert "PENDING" in str(active_index.dialect_options["postgresql"]["where"])
    assert "RUNNING" in str(active_index.dialect_options["postgresql"]["where"])


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SummaryJobStatus.PENDING, SummaryJobStatus.RUNNING),
        (SummaryJobStatus.RUNNING, SummaryJobStatus.SUCCEEDED),
        (SummaryJobStatus.RUNNING, SummaryJobStatus.FAILED),
        (SummaryJobStatus.FAILED, SummaryJobStatus.PENDING),
    ],
)
def test_summary_job_state_machine_allows_expected_transitions(
    current: SummaryJobStatus,
    target: SummaryJobStatus,
) -> None:
    """状态机应允许启动、完成、失败和显式重试。"""
    SummaryJobStateMachine.ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SummaryJobStatus.PENDING, SummaryJobStatus.SUCCEEDED),
        (SummaryJobStatus.SUCCEEDED, SummaryJobStatus.RUNNING),
        (SummaryJobStatus.FAILED, SummaryJobStatus.SUCCEEDED),
    ],
)
def test_summary_job_state_machine_rejects_invalid_transitions(
    current: SummaryJobStatus,
    target: SummaryJobStatus,
) -> None:
    """任务不得跳过执行或从终态重新运行。"""
    with pytest.raises(ValueError, match="不允许的摘要任务状态转换"):
        SummaryJobStateMachine.ensure_transition(current, target)


def test_repository_creates_job_without_committing() -> None:
    """Repository 只写入并刷新任务，事务仍由后续 Service 控制。"""
    db = SimpleNamespace(add=Mock(), flush=AsyncMock(), refresh=AsyncMock())
    repository = ConversationSummaryJobRepository(db)
    job = build_job()

    result = asyncio.run(repository.create(job))

    assert result is job
    db.add.assert_called_once_with(job)
    db.flush.assert_awaited_once()
    db.refresh.assert_awaited_once_with(job)
    assert not hasattr(db, "commit")


def test_repository_queries_active_job_by_conversation() -> None:
    """活动任务查询必须限制会话，并只包含待处理或运行中状态。"""
    job = build_job()
    db = SimpleNamespace(scalar=AsyncMock(return_value=job))
    repository = ConversationSummaryJobRepository(db)

    result = asyncio.run(repository.get_active_by_conversation_id(job.conversation_id))
    statement = str(db.scalar.await_args.args[0])

    assert result is job
    assert "conversation_summary_jobs.conversation_id" in statement
    assert "conversation_summary_jobs.status" in statement


def test_celery_uses_redis_broker_without_result_backend() -> None:
    """Celery 只通过 Redis 传递任务，业务状态不得依赖易丢失的结果后端。"""
    assert celery_app.conf.broker_url.startswith("redis://")
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_ignore_result is True
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_celery_long_tasks_use_late_ack_and_single_prefetch() -> None:
    """长任务应执行后确认，并限制每个进程预取数量。"""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_connection_retry_on_startup is True


def test_summary_job_migration_follows_current_head() -> None:
    """任务表迁移必须接在摘要表版本之后并建立活动任务唯一索引。"""
    migration = Path(
        "migrations/versions/e3b1c8a46f72_create_conversation_summary_jobs_table.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "d91e7f3a2c40"' in migration
    assert '"conversation_summary_jobs"' in migration
    assert '"uq_conversation_summary_jobs_active_conversation"' in migration
    assert 'postgresql_where=sa.text("status IN (\'PENDING\', \'RUNNING\')")' in migration
    assert "summary_job_status.create" not in migration
