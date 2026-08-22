from uuid import UUID

from app.core.config import settings
from app.core.loggers import app_logger
from app.infrastructure.database.session import SessionLocal, engine
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.object_storage.provider import get_object_storage
from app.infrastructure.task_queue.celery_app import celery_app
from app.infrastructure.task_queue.asyncio_runner import run_async
from app.infrastructure.task_queue.document_ingestion_dispatcher import (
    CeleryDocumentIngestionDispatcher,
)
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.processing.ocr_provider import RapidOcrProvider
from app.modules.knowledge.repository.document_index_repository import (
    DocumentIndexRepository,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.service.document_ingestion_failure_policy import (
    DocumentIngestionFailurePolicy,
)
from app.modules.knowledge.service.document_ingestion_dispatch_service import (
    DocumentIngestionDispatchService,
)
from app.modules.knowledge.service.document_ingestion_recovery_service import (
    DocumentIngestionRecoveryService,
)
from app.modules.knowledge.service.document_ingestion_processor import (
    DocumentIngestionProcessor,
)
from app.modules.knowledge.service.document_ingestion_worker_service import (
    DocumentIngestionWorkerService,
)


# 每个 Celery Worker 进程只持有一个懒加载 OCR Provider，避免每份文档重复加载 ONNX 模型。
_ocr_provider = RapidOcrProvider()


async def _execute_document_ingestion_job(job_id: UUID) -> str:
    """在独立异步上下文中组装并执行一个文档入库任务。"""
    try:
        async with SessionLocal() as db:
            document_repository = DocumentIndexRepository(db)
            processor = DocumentIngestionProcessor(
                document_repository=document_repository,
                object_storage=get_object_storage(),
                parser_registry=DocumentParserRegistry(
                    ocr_provider=_ocr_provider,
                    ocr_min_confidence=settings.ocr.min_confidence,
                    ocr_pdf_dpi=settings.ocr.pdf_dpi,
                    ocr_max_pdf_pages=settings.ocr.max_pdf_pages,
                    ocr_max_image_pixels=settings.ocr.max_image_pixels,
                    ocr_max_image_width=settings.ocr.max_image_width,
                    ocr_max_image_height=settings.ocr.max_image_height,
                    ocr_max_lines_per_section=(
                        settings.ocr.max_lines_per_section
                    ),
                    ocr_max_characters_per_section=(
                        settings.ocr.max_characters_per_section
                    ),
                ),
                embedding_factory=EmbeddingProviderFactory(),
                chunk_size_tokens=settings.rag.chunk_size_tokens,
                chunk_overlap_tokens=settings.rag.chunk_overlap_tokens,
            )
            worker = DocumentIngestionWorkerService(
                db=db,
                job_repository=DocumentIngestionJobRepository(db),
                document_repository=document_repository,
                processor=processor,
                failure_policy=DocumentIngestionFailurePolicy(
                    base_delay_seconds=(
                        settings.worker.document_retry_base_delay_seconds
                    ),
                    max_delay_seconds=(
                        settings.worker.document_retry_max_delay_seconds
                    ),
                ),
            )
            return await worker.execute(job_id)
    finally:
        # Celery 同步入口每次创建新事件循环，结束后不能把旧循环连接留在池中。
        await engine.dispose()


async def _recover_document_ingestion_jobs() -> dict[str, int]:
    """组装恢复扫描依赖，并返回本轮任务统计。"""
    try:
        async with SessionLocal() as db:
            job_repository = DocumentIngestionJobRepository(db)
            document_repository = DocumentIndexRepository(db)
            dispatch_service = DocumentIngestionDispatchService(
                db=db,
                job_repository=job_repository,
                dispatcher=CeleryDocumentIngestionDispatcher(),
                dispatch_lease_seconds=(
                    settings.worker.document_dispatch_lease_seconds
                ),
            )
            recovery_service = DocumentIngestionRecoveryService(
                db=db,
                job_repository=job_repository,
                document_repository=document_repository,
                dispatch_service=dispatch_service,
                running_timeout_seconds=(
                    settings.worker.document_running_timeout_seconds
                ),
                stale_retry_delay_seconds=(
                    settings.worker.document_retry_base_delay_seconds
                ),
                batch_size=settings.worker.document_recovery_batch_size,
            )
            result = await recovery_service.recover()
            return {
                "failed_exhausted": result.failed_exhausted,
                "requeued_stale": result.requeued_stale,
                "dispatch_candidates": result.dispatch_candidates,
                "dispatched": result.dispatched,
            }
    finally:
        # Beat 同样创建独立事件循环，结束时释放该循环拥有的数据库连接。
        await engine.dispose()


@celery_app.task(name="knowledge.process_document_ingestion_job", ignore_result=True)
def process_document_ingestion_job(job_id: str) -> str:
    """把 Celery 同步消息桥接到异步文档入库 Worker。"""
    try:
        parsed_job_id = UUID(job_id)
    except ValueError:
        app_logger.warning("收到非法文档入库任务 ID，已跳过 job_id=%s", job_id)
        return "invalid_job_id"
    return run_async(_execute_document_ingestion_job(parsed_job_id))


@celery_app.task(name="knowledge.recover_document_ingestion_jobs", ignore_result=True)
def recover_document_ingestion_jobs() -> dict[str, int]:
    """周期扫描 PostgreSQL，恢复卡死并重投到期文档任务。"""
    return run_async(_recover_document_ingestion_jobs())
