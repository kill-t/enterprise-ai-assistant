from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.modules.knowledge.repository.document_index_repository import (
    DocumentIndexRepository,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.service.document_ingestion_dispatch_service import (
    DocumentIngestionDispatchService,
)


@dataclass(frozen=True, slots=True)
class DocumentIngestionRecoveryResult:
    """汇总一轮文档任务恢复和投递数量。"""

    failed_exhausted: int
    requeued_stale: int
    dispatch_candidates: int
    dispatched: int


class DocumentIngestionRecoveryService:
    """恢复卡死 RUNNING，并重新投递到期 PENDING 文档任务。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: DocumentIngestionJobRepository,
        document_repository: DocumentIndexRepository,
        dispatch_service: DocumentIngestionDispatchService,
        running_timeout_seconds: int,
        stale_retry_delay_seconds: int,
        batch_size: int,
    ) -> None:
        """配置运行超时、崩溃重试延迟和单轮处理上限。"""
        if min(running_timeout_seconds, stale_retry_delay_seconds, batch_size) <= 0:
            raise ValueError("文档入库恢复配置必须全部大于零")
        self.db = db
        self.job_repository = job_repository
        self.document_repository = document_repository
        self.dispatch_service = dispatch_service
        self.running_timeout_seconds = running_timeout_seconds
        self.stale_retry_delay_seconds = stale_retry_delay_seconds
        self.batch_size = batch_size

    async def recover(
        self,
        now: datetime | None = None,
    ) -> DocumentIngestionRecoveryResult:
        """同步修正卡死任务和文档状态，再发布本轮到期任务。"""
        scan_time = now or datetime.now(timezone.utc)
        stale_before = scan_time - timedelta(seconds=self.running_timeout_seconds)

        failed_jobs = await self.job_repository.fail_exhausted_stale_running(
            stale_before=stale_before,
            failed_at=scan_time,
            batch_size=self.batch_size,
        )
        await self.document_repository.mark_recovery_failed(
            document_ids=[job.document_id for job in failed_jobs],
            failed_at=scan_time,
            error_code="DOCUMENT_WORKER_LOST_MAX_ATTEMPTS",
            error_message="文档入库进程中断且已达到最大次数",
        )
        requeued_jobs = await self.job_repository.requeue_stale_running(
            stale_before=stale_before,
            failed_at=scan_time,
            next_retry_at=(
                scan_time + timedelta(seconds=self.stale_retry_delay_seconds)
            ),
            batch_size=self.batch_size,
        )
        await self.document_repository.mark_recovery_pending(
            document_ids=[job.document_id for job in requeued_jobs],
            updated_at=scan_time,
            error_code="DOCUMENT_WORKER_LOST",
            error_message="文档入库进程中断，等待恢复",
        )
        # 任务与文档状态在同一事务提交，不能出现 job PENDING、document PROCESSING。
        await self._commit()

        due_job_ids = await self.job_repository.list_due_dispatch_ids(
            now=scan_time,
            batch_size=self.batch_size,
        )
        # 释放只读事务后再等待 Redis Broker，避免扫描器长期占用数据库连接。
        await self._commit()

        dispatched = 0
        for job_id in due_job_ids:
            result = await self.dispatch_service.dispatch_if_due(
                job_id=job_id,
                now=scan_time,
            )
            if result == "dispatched":
                dispatched += 1

        recovery_result = DocumentIngestionRecoveryResult(
            failed_exhausted=len(failed_jobs),
            requeued_stale=len(requeued_jobs),
            dispatch_candidates=len(due_job_ids),
            dispatched=dispatched,
        )
        app_logger.info(
            (
                "文档入库恢复扫描完成 failed_exhausted=%s requeued_stale=%s "
                "dispatch_candidates=%s dispatched=%s"
            ),
            recovery_result.failed_exhausted,
            recovery_result.requeued_stale,
            recovery_result.dispatch_candidates,
            recovery_result.dispatched,
        )
        return recovery_result

    async def _commit(self) -> None:
        """提交恢复扫描事务，失败时回滚 Session。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
