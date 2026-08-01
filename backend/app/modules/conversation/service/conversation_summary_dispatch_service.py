from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.infrastructure.task_queue.summary_job_dispatcher import SummaryJobDispatcher
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)


class ConversationSummaryDispatchService:
    """使用 PostgreSQL 投递租约协调任务发布，避免多个发布者重复发送。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: ConversationSummaryJobRepository,
        dispatcher: SummaryJobDispatcher,
        dispatch_lease_seconds: int,
    ) -> None:
        """注入事务、任务仓库、Broker 发布器和投递租约时长。"""
        if dispatch_lease_seconds <= 0:
            raise ValueError("摘要投递租约必须大于零")
        self.db = db
        self.job_repository = job_repository
        self.dispatcher = dispatcher
        self.dispatch_lease_seconds = dispatch_lease_seconds

    async def dispatch_if_due(
        self,
        job_id: UUID,
        now: datetime | None = None,
    ) -> str:
        """原子领取投递权后发布消息，并可靠记录或释放本次租约。"""
        claimed_at = now or datetime.now(timezone.utc)
        claimed_job = await self.job_repository.claim_dispatch(
            job_id=job_id,
            claimed_at=claimed_at,
            lease_expired_before=(
                claimed_at - timedelta(seconds=self.dispatch_lease_seconds)
            ),
        )
        # 先提交租约，其他发布者才能看见当前任务已经有人负责投递。
        await self._commit()
        if claimed_job is None:
            return "skipped"

        try:
            celery_task_id = await self.dispatcher.dispatch(job_id)
        except Exception as exc:
            app_logger.warning(
                "摘要任务 Broker 发布失败 job_id=%s reason=%s",
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
            app_logger.warning("摘要任务投递记录已过期 job_id=%s", job_id)
            return "stale"

        app_logger.info(
            "摘要任务已投递 job_id=%s celery_task_id=%s",
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
