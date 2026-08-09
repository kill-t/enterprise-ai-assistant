from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.infrastructure.task_queue.document_ingestion_dispatcher import (
    DocumentIngestionDispatcher,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)


class DocumentIngestionDispatchService:
    """使用 PostgreSQL 投递租约协调文档任务发布。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: DocumentIngestionJobRepository,
        dispatcher: DocumentIngestionDispatcher,
        dispatch_lease_seconds: int,
    ) -> None:
        """注入事务、任务仓库、Broker 发布器和租约时长。"""
        if dispatch_lease_seconds <= 0:
            raise ValueError("文档投递租约必须大于零")
        self.db = db
        self.job_repository = job_repository
        self.dispatcher = dispatcher
        self.dispatch_lease_seconds = dispatch_lease_seconds

    async def dispatch_if_due(
        self,
        job_id: UUID,
        now: datetime | None = None,
    ) -> str:
        """领取租约后发布任务，并可靠写回结果或释放租约。"""
        claimed_at = now or datetime.now(timezone.utc)
        claimed_job = await self.job_repository.claim_dispatch(
            job_id=job_id,
            claimed_at=claimed_at,
            lease_expired_before=(
                claimed_at - timedelta(seconds=self.dispatch_lease_seconds)
            ),
        )
        # 租约必须先提交，使其他 API 实例和恢复扫描器能观察到执行权。
        await self._commit()
        if claimed_job is None:
            return "skipped"

        try:
            celery_task_id = await self.dispatcher.dispatch(job_id)
        except Exception as exc:
            app_logger.warning(
                "文档入库任务 Broker 发布失败 job_id=%s reason=%s",
                job_id,
                type(exc).__name__,
                exc_info=True,
            )
            await self.job_repository.release_dispatch_claim(
                job_id=job_id,
                claimed_at=claimed_at,
            )
            await self._commit()
            return "publish_failed"

        marked_job = await self.job_repository.mark_dispatched(
            job_id=job_id,
            claimed_at=claimed_at,
            celery_task_id=celery_task_id,
        )
        await self._commit()
        if marked_job is None:
            app_logger.warning("文档入库任务投递记录已过期 job_id=%s", job_id)
            return "stale"

        app_logger.info(
            "文档入库任务已投递 job_id=%s celery_task_id=%s",
            job_id,
            celery_task_id,
        )
        return "dispatched"

    async def _commit(self) -> None:
        """提交投递阶段事务，失败时回滚当前 Session。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
