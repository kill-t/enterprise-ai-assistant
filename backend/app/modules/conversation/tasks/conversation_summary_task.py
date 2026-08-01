import asyncio
from uuid import UUID

from app.core.loggers import app_logger
from app.core.config import settings
from app.infrastructure.database.session import SessionLocal, engine
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.infrastructure.task_queue.celery_app import celery_app
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.repository.conversation_summary_repository import (
    ConversationSummaryRepository,
)
from app.modules.conversation.repository.message_repository import MessageRepository
from app.modules.conversation.service.conversation_summary_processor import (
    RollingConversationSummaryProcessor,
)
from app.modules.conversation.service.conversation_summary_worker_service import (
    ConversationSummaryWorkerService,
)
from app.modules.conversation.service.summary_failure_policy import (
    SummaryFailurePolicy,
)


async def _execute_summary_job(job_id: UUID) -> str:
    """在独立异步上下文中组装数据库依赖并执行摘要任务。"""
    try:
        async with SessionLocal() as db:
            repository = ConversationSummaryJobRepository(db)
            context_settings = ProviderFactory.get_context_settings()
            processor = RollingConversationSummaryProcessor(
                message_repository=MessageRepository(db),
                summary_repository=ConversationSummaryRepository(db),
                provider=ProviderFactory.create(),
                model_name=ProviderFactory.get_model_name(),
                input_budget_tokens=context_settings.available_input_tokens,
            )
            service = ConversationSummaryWorkerService(
                db=db,
                job_repository=repository,
                processor=processor,
                failure_policy=SummaryFailurePolicy(
                    base_delay_seconds=(
                        settings.worker.summary_retry_base_delay_seconds
                    ),
                    max_delay_seconds=(
                        settings.worker.summary_retry_max_delay_seconds
                    ),
                ),
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
