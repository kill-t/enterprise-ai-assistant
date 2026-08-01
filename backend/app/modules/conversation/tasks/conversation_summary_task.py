import asyncio
from uuid import UUID

from app.core.loggers import app_logger
from app.infrastructure.database.session import SessionLocal, engine
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.conversation_summary_worker_service import (
    ConversationSummaryWorkerService,
)
from app.modules.conversation.service.deferred_summary_processor import (
    DeferredConversationSummaryProcessor,
)


async def _execute_summary_job(job_id: UUID) -> str:
    """在独立异步上下文中组装数据库依赖并执行摘要任务。"""
    try:
        async with SessionLocal() as db:
            repository = ConversationSummaryJobRepository(db)
            service = ConversationSummaryWorkerService(
                db=db,
                job_repository=repository,
                processor=DeferredConversationSummaryProcessor(),
            )
            return await service.execute(job_id)
    finally:
        # Celery 任务是同步入口，每次 asyncio.run 都会创建并关闭事件循环。
        # 主动释放连接池，避免连接被下一个任务的不同事件循环继续复用。
        await engine.dispose()


@celery_app.task(name="conversation.process_summary_job", ignore_result=True)
def process_conversation_summary_job(job_id: str) -> str:
    """把 Celery 的同步任务入口桥接到项目的异步 Worker Service。"""
    try:
        parsed_job_id = UUID(job_id)
    except ValueError:
        app_logger.warning("收到非法摘要任务 ID，已跳过 job_id=%s", job_id)
        return "invalid_job_id"

    return asyncio.run(_execute_summary_job(parsed_job_id))
