import asyncio
from uuid import UUID

from app.core.config import settings
from app.core.loggers import app_logger
from app.infrastructure.database.session import SessionLocal, engine
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.object_storage.provider import get_object_storage
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.repository.document_index_repository import (
    DocumentIndexRepository,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.service.document_ingestion_failure_policy import (
    DocumentIngestionFailurePolicy,
)
from app.modules.knowledge.service.document_ingestion_processor import (
    DocumentIngestionProcessor,
)
from app.modules.knowledge.service.document_ingestion_worker_service import (
    DocumentIngestionWorkerService,
)


async def _execute_document_ingestion_job(job_id: UUID) -> str:
    """在独立异步上下文中组装并执行一个文档入库任务。"""
    try:
        async with SessionLocal() as db:
            document_repository = DocumentIndexRepository(db)
            processor = DocumentIngestionProcessor(
                document_repository=document_repository,
                object_storage=get_object_storage(),
                parser_registry=DocumentParserRegistry(),
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


@celery_app.task(name="knowledge.process_document_ingestion_job", ignore_result=True)
def process_document_ingestion_job(job_id: str) -> str:
    """把 Celery 同步消息桥接到异步文档入库 Worker。"""
    try:
        parsed_job_id = UUID(job_id)
    except ValueError:
        app_logger.warning("收到非法文档入库任务 ID，已跳过 job_id=%s", job_id)
        return "invalid_job_id"
    return asyncio.run(_execute_document_ingestion_job(parsed_job_id))
