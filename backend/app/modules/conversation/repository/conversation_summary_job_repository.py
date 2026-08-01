from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import SummaryJobStatus
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)


class ConversationSummaryJobRepository:
    """封装摘要任务状态的异步数据库访问，不控制事务提交。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存任务 Service 使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        job: ConversationSummaryJobEntity,
    ) -> ConversationSummaryJobEntity:
        """新增持久化任务并刷新生成字段，但不提交事务。"""
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> ConversationSummaryJobEntity | None:
        """按任务 ID 查询状态，不存在时返回 None。"""
        statement = select(ConversationSummaryJobEntity).where(
            ConversationSummaryJobEntity.id == job_id
        )
        return await self.db.scalar(statement)

    async def get_active_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> ConversationSummaryJobEntity | None:
        """查询会话当前待处理或运行中的唯一摘要任务。"""
        statement = select(ConversationSummaryJobEntity).where(
            ConversationSummaryJobEntity.conversation_id == conversation_id,
            ConversationSummaryJobEntity.status.in_(
                (SummaryJobStatus.PENDING, SummaryJobStatus.RUNNING)
            ),
        )
        return await self.db.scalar(statement)

    async def update(
        self,
        job: ConversationSummaryJobEntity,
    ) -> ConversationSummaryJobEntity:
        """刷新上层已修改的任务状态，不隐式提交事务。"""
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def claim_pending(
        self,
        job_id: UUID,
        started_at: datetime,
    ) -> ConversationSummaryJobEntity | None:
        """用一条条件 UPDATE 原子领取待处理任务，避免多个 Worker 重复执行。

        这里故意保留 ``dispatch_claimed_at``：Worker 可能在发布方拿到 Broker
        返回值之前就执行完成，保留租约可让发布方随后仍能回写 Celery task ID。
        只有任务准备进入下一次重试时，才清除旧租约。
        """
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.PENDING,
                (
                    ConversationSummaryJobEntity.attempts
                    < ConversationSummaryJobEntity.max_attempts
                ),
                or_(
                    ConversationSummaryJobEntity.next_retry_at.is_(None),
                    ConversationSummaryJobEntity.next_retry_at <= started_at,
                ),
            )
            .values(
                status=SummaryJobStatus.RUNNING,
                attempts=ConversationSummaryJobEntity.attempts + 1,
                started_at=started_at,
                finished_at=None,
                error_code=None,
                error_message=None,
                next_retry_at=None,
            )
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_succeeded(
        self,
        job_id: UUID,
        expected_attempts: int,
        finished_at: datetime,
    ) -> ConversationSummaryJobEntity | None:
        """仅允许持有相同执行次数的 Worker 把运行中任务标记为成功。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
                ConversationSummaryJobEntity.attempts == expected_attempts,
            )
            .values(
                status=SummaryJobStatus.SUCCEEDED,
                finished_at=finished_at,
                error_code=None,
                error_message=None,
                next_retry_at=None,
            )
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_retry_pending(
        self,
        job_id: UUID,
        expected_attempts: int,
        failed_at: datetime,
        next_retry_at: datetime,
        error_code: str,
        error_message: str,
    ) -> ConversationSummaryJobEntity | None:
        """把可重试失败原子恢复为延迟 PENDING，并清除上次投递标记。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
                ConversationSummaryJobEntity.attempts == expected_attempts,
                (
                    ConversationSummaryJobEntity.attempts
                    < ConversationSummaryJobEntity.max_attempts
                ),
            )
            .values(
                status=SummaryJobStatus.PENDING,
                celery_task_id=None,
                started_at=None,
                finished_at=failed_at,
                next_retry_at=next_retry_at,
                error_code=error_code,
                error_message=error_message,
                dispatch_claimed_at=None,
            )
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_failed(
        self,
        job_id: UUID,
        expected_attempts: int,
        finished_at: datetime,
        error_code: str,
        error_message: str,
    ) -> ConversationSummaryJobEntity | None:
        """按执行次数记录失败结果，防止迟到 Worker 覆盖较新的执行状态。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
                ConversationSummaryJobEntity.attempts == expected_attempts,
            )
            .values(
                status=SummaryJobStatus.FAILED,
                finished_at=finished_at,
                error_code=error_code,
                error_message=error_message,
                next_retry_at=None,
            )
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def claim_dispatch(
        self,
        job_id: UUID,
        claimed_at: datetime,
        lease_expired_before: datetime,
    ) -> ConversationSummaryJobEntity | None:
        """原子领取到期 PENDING 的投递租约，旧租约超时后允许重新接管。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.PENDING,
                (
                    ConversationSummaryJobEntity.attempts
                    < ConversationSummaryJobEntity.max_attempts
                ),
                or_(
                    ConversationSummaryJobEntity.next_retry_at.is_(None),
                    ConversationSummaryJobEntity.next_retry_at <= claimed_at,
                ),
                or_(
                    ConversationSummaryJobEntity.dispatch_claimed_at.is_(None),
                    (
                        ConversationSummaryJobEntity.dispatch_claimed_at
                        <= lease_expired_before
                    ),
                ),
            )
            .values(
                dispatch_claimed_at=claimed_at,
                celery_task_id=None,
            )
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_dispatched(
        self,
        job_id: UUID,
        claimed_at: datetime,
        celery_task_id: str,
    ) -> ConversationSummaryJobEntity | None:
        """只允许当前投递租约记录 Broker 接受的 Celery task ID。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.dispatch_claimed_at == claimed_at,
            )
            .values(celery_task_id=celery_task_id)
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def release_dispatch_claim(
        self,
        job_id: UUID,
        claimed_at: datetime,
    ) -> ConversationSummaryJobEntity | None:
        """Broker 发布失败时释放当前租约，使后续请求或扫描能够重试。"""
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id == job_id,
                ConversationSummaryJobEntity.status == SummaryJobStatus.PENDING,
                ConversationSummaryJobEntity.dispatch_claimed_at == claimed_at,
                ConversationSummaryJobEntity.celery_task_id.is_(None),
            )
            .values(dispatch_claimed_at=None)
            .returning(ConversationSummaryJobEntity)
        )
        return await self.db.scalar(statement)

    async def list_due_dispatch_ids(
        self,
        now: datetime,
        batch_size: int,
    ) -> list[UUID]:
        """按时间顺序返回到期 PENDING 的候选 ID，最终投递权仍需原子领取。"""
        statement = (
            select(ConversationSummaryJobEntity.id)
            .where(
                ConversationSummaryJobEntity.status == SummaryJobStatus.PENDING,
                (
                    ConversationSummaryJobEntity.attempts
                    < ConversationSummaryJobEntity.max_attempts
                ),
                or_(
                    ConversationSummaryJobEntity.next_retry_at.is_(None),
                    ConversationSummaryJobEntity.next_retry_at <= now,
                ),
            )
            .order_by(
                ConversationSummaryJobEntity.next_retry_at.asc().nullsfirst(),
                ConversationSummaryJobEntity.created_at,
                ConversationSummaryJobEntity.id,
            )
            .limit(batch_size)
        )
        return list((await self.db.scalars(statement)).all())

    async def fail_exhausted_stale_running(
        self,
        stale_before: datetime,
        failed_at: datetime,
        batch_size: int,
    ) -> list[ConversationSummaryJobEntity]:
        """把超时且次数耗尽的 RUNNING 批量标记为永久失败。"""
        candidate_ids = (
            select(ConversationSummaryJobEntity.id)
            .where(
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
                ConversationSummaryJobEntity.started_at <= stale_before,
                (
                    ConversationSummaryJobEntity.attempts
                    >= ConversationSummaryJobEntity.max_attempts
                ),
            )
            .order_by(ConversationSummaryJobEntity.started_at)
            .limit(batch_size)
        )
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id.in_(candidate_ids),
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
            )
            .values(
                status=SummaryJobStatus.FAILED,
                finished_at=failed_at,
                next_retry_at=None,
                dispatch_claimed_at=None,
                error_code="SUMMARY_WORKER_LOST_MAX_ATTEMPTS",
                error_message="摘要任务执行进程中断且已达到最大次数",
            )
            .returning(ConversationSummaryJobEntity)
        )
        return list((await self.db.scalars(statement)).all())

    async def requeue_stale_running(
        self,
        stale_before: datetime,
        failed_at: datetime,
        next_retry_at: datetime,
        batch_size: int,
    ) -> list[ConversationSummaryJobEntity]:
        """把超时且仍有次数的 RUNNING 批量恢复为延迟 PENDING。"""
        candidate_ids = (
            select(ConversationSummaryJobEntity.id)
            .where(
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
                ConversationSummaryJobEntity.started_at <= stale_before,
                (
                    ConversationSummaryJobEntity.attempts
                    < ConversationSummaryJobEntity.max_attempts
                ),
            )
            .order_by(ConversationSummaryJobEntity.started_at)
            .limit(batch_size)
        )
        statement = (
            update(ConversationSummaryJobEntity)
            .where(
                ConversationSummaryJobEntity.id.in_(candidate_ids),
                ConversationSummaryJobEntity.status == SummaryJobStatus.RUNNING,
            )
            .values(
                status=SummaryJobStatus.PENDING,
                celery_task_id=None,
                dispatch_claimed_at=None,
                started_at=None,
                finished_at=failed_at,
                next_retry_at=next_retry_at,
                error_code="SUMMARY_WORKER_LOST",
                error_message="摘要任务执行进程中断，等待恢复",
            )
            .returning(ConversationSummaryJobEntity)
        )
        return list((await self.db.scalars(statement)).all())
