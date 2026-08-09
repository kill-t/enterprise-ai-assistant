from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.modules.knowledge.entity.document_ingestion_job import (
    DocumentIngestionJobEntity,
)
from app.modules.knowledge.processing.ingestion_schemas import (
    DocumentIngestionSource,
    ProcessedDocumentIngestion,
)
from app.modules.knowledge.repository.document_index_repository import (
    DocumentIndexRepository,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.service.document_ingestion_failure_policy import (
    DocumentIngestionFailurePolicy,
    DocumentSourceNotFoundError,
)


class IngestionProcessor(Protocol):
    """描述 Worker 所需的准备、外部处理和暂存三个阶段。"""

    async def prepare(
        self,
        job: DocumentIngestionJobEntity,
    ) -> DocumentIngestionSource:
        """复制任务所需数据库数据。"""
        ...

    async def process(
        self,
        source: DocumentIngestionSource,
    ) -> ProcessedDocumentIngestion:
        """在数据库事务外完成耗时处理。"""
        ...

    async def persist(
        self,
        source: DocumentIngestionSource,
        processed: ProcessedDocumentIngestion,
        finished_at: datetime,
    ) -> None:
        """暂存等待最终原子提交的索引结果。"""
        ...


class DocumentIngestionWorkerService:
    """编排入库任务领取、处理、重试和原子收尾，不依赖 Celery API。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: DocumentIngestionJobRepository,
        document_repository: DocumentIndexRepository,
        processor: IngestionProcessor,
        failure_policy: DocumentIngestionFailurePolicy | None = None,
    ) -> None:
        """注入事务、任务仓库、文档仓库和可替换处理器。"""
        self.db = db
        self.job_repository = job_repository
        self.document_repository = document_repository
        self.processor = processor
        self.failure_policy = failure_policy or DocumentIngestionFailurePolicy()

    async def execute(self, job_id: UUID) -> str:
        """执行一次持久化任务，并返回稳定的执行结果。"""
        started_at = datetime.now(timezone.utc)
        claimed_job = await self.job_repository.claim_pending(job_id, started_at)
        if claimed_job is None:
            # 即使 UPDATE 没命中也结束当前事务，释放数据库连接上的快照。
            await self._commit()
            app_logger.info("文档入库任务未取得执行权，已跳过 job_id=%s", job_id)
            return "skipped"

        processing_document_id = await self.document_repository.mark_processing(
            document_id=claimed_job.document_id,
            started_at=started_at,
        )
        # 先提交执行权和 PROCESSING，其他 Worker 才能观察并跳过重复消息。
        await self._commit()
        if processing_document_id is None:
            return await self._handle_failure(
                claimed_job,
                DocumentSourceNotFoundError(),
            )

        attempts = claimed_job.attempts
        app_logger.info(
            "文档入库任务已取得执行权 job_id=%s document_id=%s attempts=%s",
            job_id,
            claimed_job.document_id,
            attempts,
        )
        try:
            source = await self.processor.prepare(claimed_job)
            # ORM 数据已经复制成不可变结构，提交只读事务后再开始耗时外部 I/O。
            await self._commit()
            processed = await self.processor.process(source)
            finished_at = datetime.now(timezone.utc)
            await self.processor.persist(source, processed, finished_at)
        except Exception as exc:
            return await self._handle_failure(claimed_job, exc)

        succeeded_job = await self.job_repository.mark_succeeded(
            job_id=job_id,
            expected_attempts=attempts,
            finished_at=finished_at,
        )
        if succeeded_job is None:
            # 任务执行版本已变化，回滚本 Worker 暂存的 Chunk 和 READY 状态。
            await self.db.rollback()
            app_logger.warning(
                "文档入库成功结果已过期，已回滚 job_id=%s attempts=%s",
                job_id,
                attempts,
            )
            return "stale"

        await self._commit()
        app_logger.info(
            "文档入库任务处理成功 job_id=%s document_id=%s chunks=%s",
            job_id,
            claimed_job.document_id,
            len(processed.chunks),
        )
        return "succeeded"

    async def _handle_failure(
        self,
        claimed_job: DocumentIngestionJobEntity,
        exc: Exception,
    ) -> str:
        """回滚半成品，并原子保存重试或永久失败状态。"""
        app_logger.error(
            "文档入库任务处理异常 job_id=%s document_id=%s attempts=%s reason=%s",
            claimed_job.id,
            claimed_job.document_id,
            claimed_job.attempts,
            type(exc).__name__,
            exc_info=True,
        )
        await self.db.rollback()
        failed_at = datetime.now(timezone.utc)
        decision = self.failure_policy.classify(exc)

        if decision.retryable and claimed_job.attempts < claimed_job.max_attempts:
            next_retry_at = self.failure_policy.next_retry_at(
                attempts=claimed_job.attempts,
                now=failed_at,
            )
            retry_job = await self.job_repository.mark_retry_pending(
                job_id=claimed_job.id,
                expected_attempts=claimed_job.attempts,
                failed_at=failed_at,
                next_retry_at=next_retry_at,
                error_code=decision.error_code,
                error_message=decision.error_message,
            )
            retry_document = await self.document_repository.mark_retry_pending(
                document_id=claimed_job.document_id,
                error_code=decision.error_code,
                error_message=decision.error_message,
                updated_at=failed_at,
            )
            if retry_job is None or retry_document is None:
                await self.db.rollback()
                return "stale"
            await self._commit()
            app_logger.warning(
                "文档入库任务已安排延迟重试 job_id=%s next_retry_at=%s",
                claimed_job.id,
                next_retry_at,
            )
            return "retry_scheduled"

        failed_job = await self.job_repository.mark_failed(
            job_id=claimed_job.id,
            expected_attempts=claimed_job.attempts,
            finished_at=failed_at,
            error_code=decision.error_code,
            error_message=decision.error_message,
        )
        failed_document = await self.document_repository.mark_failed(
            document_id=claimed_job.document_id,
            error_code=decision.error_code,
            error_message=decision.error_message,
            finished_at=failed_at,
        )
        if failed_job is None or failed_document is None:
            await self.db.rollback()
            return "stale"
        await self._commit()
        return "failed"

    async def _commit(self) -> None:
        """提交当前阶段，提交失败时先恢复 Session 再向上抛出。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
