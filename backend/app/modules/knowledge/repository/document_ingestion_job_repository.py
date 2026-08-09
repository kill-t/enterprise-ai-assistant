from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentIngestionJobStatus
from app.modules.knowledge.entity.document_ingestion_job import (
    DocumentIngestionJobEntity,
)


class DocumentIngestionJobRepository:
    """封装文档入库任务的原子状态转换，不负责提交事务。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存 Worker 使用的异步数据库 Session。"""
        self.db = db

    async def claim_pending(
        self,
        job_id: UUID,
        started_at: datetime,
    ) -> DocumentIngestionJobEntity | None:
        """原子领取到期 PENDING 任务，并递增 attempts 防止重复执行。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.PENDING
                ),
                (
                    DocumentIngestionJobEntity.attempts
                    < DocumentIngestionJobEntity.max_attempts
                ),
                or_(
                    DocumentIngestionJobEntity.next_retry_at.is_(None),
                    DocumentIngestionJobEntity.next_retry_at <= started_at,
                ),
            )
            .values(
                status=DocumentIngestionJobStatus.RUNNING,
                attempts=DocumentIngestionJobEntity.attempts + 1,
                started_at=started_at,
                finished_at=None,
                next_retry_at=None,
                error_code=None,
                error_message=None,
            )
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_succeeded(
        self,
        job_id: UUID,
        expected_attempts: int,
        finished_at: datetime,
    ) -> DocumentIngestionJobEntity | None:
        """只允许同一 attempts 的 RUNNING Worker 写入成功终态。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
                DocumentIngestionJobEntity.attempts == expected_attempts,
            )
            .values(
                status=DocumentIngestionJobStatus.SUCCEEDED,
                finished_at=finished_at,
                next_retry_at=None,
                error_code=None,
                error_message=None,
            )
            .returning(DocumentIngestionJobEntity)
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
    ) -> DocumentIngestionJobEntity | None:
        """把临时失败恢复为延迟 PENDING，并清除旧投递信息。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
                DocumentIngestionJobEntity.attempts == expected_attempts,
                (
                    DocumentIngestionJobEntity.attempts
                    < DocumentIngestionJobEntity.max_attempts
                ),
            )
            .values(
                status=DocumentIngestionJobStatus.PENDING,
                celery_task_id=None,
                dispatch_claimed_at=None,
                started_at=None,
                finished_at=failed_at,
                next_retry_at=next_retry_at,
                error_code=error_code,
                error_message=error_message,
            )
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_failed(
        self,
        job_id: UUID,
        expected_attempts: int,
        finished_at: datetime,
        error_code: str,
        error_message: str,
    ) -> DocumentIngestionJobEntity | None:
        """按 attempts 保存永久失败，防止迟到 Worker 覆盖新状态。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
                DocumentIngestionJobEntity.attempts == expected_attempts,
            )
            .values(
                status=DocumentIngestionJobStatus.FAILED,
                finished_at=finished_at,
                next_retry_at=None,
                error_code=error_code,
                error_message=error_message,
            )
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def claim_dispatch(
        self,
        job_id: UUID,
        claimed_at: datetime,
        lease_expired_before: datetime,
    ) -> DocumentIngestionJobEntity | None:
        """原子领取到期 PENDING 的投递租约，过期租约允许重新接管。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.PENDING
                ),
                (
                    DocumentIngestionJobEntity.attempts
                    < DocumentIngestionJobEntity.max_attempts
                ),
                or_(
                    DocumentIngestionJobEntity.next_retry_at.is_(None),
                    DocumentIngestionJobEntity.next_retry_at <= claimed_at,
                ),
                or_(
                    DocumentIngestionJobEntity.dispatch_claimed_at.is_(None),
                    (
                        DocumentIngestionJobEntity.dispatch_claimed_at
                        <= lease_expired_before
                    ),
                ),
            )
            .values(
                dispatch_claimed_at=claimed_at,
                celery_task_id=None,
            )
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def mark_dispatched(
        self,
        job_id: UUID,
        claimed_at: datetime,
        celery_task_id: str,
    ) -> DocumentIngestionJobEntity | None:
        """仅允许当前租约写回 Broker 接受的 Celery task ID。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                DocumentIngestionJobEntity.dispatch_claimed_at == claimed_at,
            )
            .values(celery_task_id=celery_task_id)
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def release_dispatch_claim(
        self,
        job_id: UUID,
        claimed_at: datetime,
    ) -> DocumentIngestionJobEntity | None:
        """Broker 发布失败时释放当前租约，使下一轮扫描可立即重试。"""
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id == job_id,
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.PENDING
                ),
                DocumentIngestionJobEntity.dispatch_claimed_at == claimed_at,
                DocumentIngestionJobEntity.celery_task_id.is_(None),
            )
            .values(dispatch_claimed_at=None)
            .returning(DocumentIngestionJobEntity)
        )
        return await self.db.scalar(statement)

    async def list_due_dispatch_ids(
        self,
        now: datetime,
        batch_size: int,
    ) -> list[UUID]:
        """有序返回到期 PENDING 候选，最终发布权仍由租约 UPDATE 决定。"""
        statement = (
            select(DocumentIngestionJobEntity.id)
            .where(
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.PENDING
                ),
                (
                    DocumentIngestionJobEntity.attempts
                    < DocumentIngestionJobEntity.max_attempts
                ),
                or_(
                    DocumentIngestionJobEntity.next_retry_at.is_(None),
                    DocumentIngestionJobEntity.next_retry_at <= now,
                ),
            )
            .order_by(
                DocumentIngestionJobEntity.next_retry_at.asc().nullsfirst(),
                DocumentIngestionJobEntity.created_at,
                DocumentIngestionJobEntity.id,
            )
            .limit(batch_size)
        )
        return list((await self.db.scalars(statement)).all())

    async def fail_exhausted_stale_running(
        self,
        stale_before: datetime,
        failed_at: datetime,
        batch_size: int,
    ) -> list[DocumentIngestionJobEntity]:
        """批量终止进程中断且执行次数已经耗尽的 RUNNING 任务。"""
        candidate_ids = (
            select(DocumentIngestionJobEntity.id)
            .where(
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
                DocumentIngestionJobEntity.started_at <= stale_before,
                (
                    DocumentIngestionJobEntity.attempts
                    >= DocumentIngestionJobEntity.max_attempts
                ),
            )
            .order_by(DocumentIngestionJobEntity.started_at)
            .limit(batch_size)
        )
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id.in_(candidate_ids),
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
            )
            .values(
                status=DocumentIngestionJobStatus.FAILED,
                finished_at=failed_at,
                next_retry_at=None,
                dispatch_claimed_at=None,
                error_code="DOCUMENT_WORKER_LOST_MAX_ATTEMPTS",
                error_message="文档入库进程中断且已达到最大次数",
            )
            .returning(DocumentIngestionJobEntity)
        )
        return list((await self.db.scalars(statement)).all())

    async def requeue_stale_running(
        self,
        stale_before: datetime,
        failed_at: datetime,
        next_retry_at: datetime,
        batch_size: int,
    ) -> list[DocumentIngestionJobEntity]:
        """批量把仍有执行次数的卡死 RUNNING 恢复为延迟 PENDING。"""
        candidate_ids = (
            select(DocumentIngestionJobEntity.id)
            .where(
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
                DocumentIngestionJobEntity.started_at <= stale_before,
                (
                    DocumentIngestionJobEntity.attempts
                    < DocumentIngestionJobEntity.max_attempts
                ),
            )
            .order_by(DocumentIngestionJobEntity.started_at)
            .limit(batch_size)
        )
        statement = (
            update(DocumentIngestionJobEntity)
            .where(
                DocumentIngestionJobEntity.id.in_(candidate_ids),
                (
                    DocumentIngestionJobEntity.status
                    == DocumentIngestionJobStatus.RUNNING
                ),
            )
            .values(
                status=DocumentIngestionJobStatus.PENDING,
                celery_task_id=None,
                dispatch_claimed_at=None,
                started_at=None,
                finished_at=failed_at,
                next_retry_at=next_retry_at,
                error_code="DOCUMENT_WORKER_LOST",
                error_message="文档入库进程中断，等待恢复",
            )
            .returning(DocumentIngestionJobEntity)
        )
        return list((await self.db.scalars(statement)).all())
