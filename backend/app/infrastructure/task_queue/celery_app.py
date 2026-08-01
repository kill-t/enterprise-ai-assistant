from celery import Celery

from app.core.config import settings


def create_celery_app() -> Celery:
    """创建只负责消息投递的 Celery 应用，业务状态保存在 PostgreSQL。"""
    app = Celery("enterprise_ai_assistant")
    app.conf.update(
        imports=("app.modules.conversation.tasks.conversation_summary_task",),
        broker_url=settings.worker.broker_url,
        result_backend=None,
        task_ignore_result=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_default_queue=settings.worker.default_queue,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        task_soft_time_limit=settings.worker.task_soft_time_limit_seconds,
        task_time_limit=settings.worker.task_time_limit_seconds,
        enable_utc=True,
        timezone="UTC",
    )
    return app


# Celery CLI 通过该模块级对象发现应用配置。
celery_app = create_celery_app()
