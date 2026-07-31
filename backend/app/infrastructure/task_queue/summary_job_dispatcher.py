import asyncio
from typing import Protocol
from uuid import UUID

from celery import Celery

from app.core.config import settings
from app.infrastructure.task_queue.celery_app import celery_app


class SummaryJobDispatcher(Protocol):
    """摘要任务 Service 所依赖的异步投递接口。"""

    async def dispatch(self, job_id: UUID) -> str:
        """投递一个只携带 job ID 的任务，并返回 Celery task ID。"""
        ...


class CelerySummaryJobDispatcher:
    """通过 Celery 将持久化摘要任务 ID 投递到专用队列。"""

    TASK_NAME = "conversation.process_summary_job"

    def __init__(
        self,
        app: Celery = celery_app,
        queue_name: str = settings.worker.default_queue,
    ) -> None:
        """保存 Celery 应用和目标队列名称。"""
        self.app = app
        self.queue_name = queue_name

    async def dispatch(self, job_id: UUID) -> str:
        """在线程中执行同步 Broker 投递，避免阻塞 FastAPI 事件循环。"""
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
