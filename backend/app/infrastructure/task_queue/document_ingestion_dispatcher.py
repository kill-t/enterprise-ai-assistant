import asyncio
from typing import Protocol
from uuid import UUID

from celery import Celery

from app.core.config import settings
from app.infrastructure.task_queue.celery_app import celery_app


class DocumentIngestionDispatcher(Protocol):
    """定义文档任务投递服务所依赖的最小 Broker 接口。"""

    async def dispatch(self, job_id: UUID) -> str:
        """只发送持久化任务 ID，并返回 Celery task ID。"""
        ...


class CeleryDocumentIngestionDispatcher:
    """把文档入库任务发送到独立 Celery 队列。"""

    TASK_NAME = "knowledge.process_document_ingestion_job"

    def __init__(
        self,
        app: Celery = celery_app,
        queue_name: str = settings.worker.document_ingestion_queue,
    ) -> None:
        """保存 Celery 应用和受控队列名称。"""
        self.app = app
        self.queue_name = queue_name

    async def dispatch(self, job_id: UUID) -> str:
        """在线程池调用同步 Celery 客户端，避免阻塞 FastAPI 事件循环。"""
        task_id = str(job_id)
        result = await asyncio.to_thread(
            self.app.send_task,
            self.TASK_NAME,
            args=[task_id],
            task_id=task_id,
            queue=self.queue_name,
            retry=False,
        )
        return result.id
