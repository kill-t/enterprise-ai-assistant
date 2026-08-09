from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, update
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
