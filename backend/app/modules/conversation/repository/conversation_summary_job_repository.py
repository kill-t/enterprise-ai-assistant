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
        """用一条条件 UPDATE 原子领取待处理任务，避免多个 Worker 重复执行。"""
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
