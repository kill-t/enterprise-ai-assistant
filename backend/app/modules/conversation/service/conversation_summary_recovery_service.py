from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.conversation_summary_dispatch_service import (
    ConversationSummaryDispatchService,
)


@dataclass(frozen=True)
class SummaryRecoveryResult:
    """汇总一次周期扫描完成的恢复和投递数量。"""

    failed_exhausted: int
    requeued_stale: int
    dispatch_candidates: int
    dispatched: int


class ConversationSummaryRecoveryService:
    """恢复卡死 RUNNING，并把数据库中到期的 PENDING 交给投递服务。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: ConversationSummaryJobRepository,
        dispatch_service: ConversationSummaryDispatchService,
        running_timeout_seconds: int,
        stale_retry_delay_seconds: int,
        batch_size: int,
    ) -> None:
        """配置运行超时、崩溃后的重试延迟和单次扫描数量。"""
        if min(running_timeout_seconds, stale_retry_delay_seconds, batch_size) <= 0:
            raise ValueError("摘要恢复配置必须全部大于零")
        self.db = db
        self.job_repository = job_repository
        self.dispatch_service = dispatch_service
        self.running_timeout_seconds = running_timeout_seconds
        self.stale_retry_delay_seconds = stale_retry_delay_seconds
        self.batch_size = batch_size

    async def recover(
        self,
        now: datetime | None = None,
    ) -> SummaryRecoveryResult:
        """执行一次有上限的卡死恢复和到期任务投递扫描。"""
        scan_time = now or datetime.now(timezone.utc)
        stale_before = scan_time - timedelta(seconds=self.running_timeout_seconds)

        failed_jobs = await self.job_repository.fail_exhausted_stale_running(
            stale_before=stale_before,
            failed_at=scan_time,
            batch_size=self.batch_size,
        )
        requeued_jobs = await self.job_repository.requeue_stale_running(
            stale_before=stale_before,
            failed_at=scan_time,
            next_retry_at=(
                scan_time + timedelta(seconds=self.stale_retry_delay_seconds)
            ),
            batch_size=self.batch_size,
        )
        await self._commit()

        due_job_ids = await self.job_repository.list_due_dispatch_ids(
            now=scan_time,
            batch_size=self.batch_size,
        )
        # 结束只读事务，不在后续 Broker 网络等待期间占用数据库连接。
        await self._commit()

        dispatched = 0
        for job_id in due_job_ids:
            result = await self.dispatch_service.dispatch_if_due(
                job_id=job_id,
                now=scan_time,
            )
            if result == "dispatched":
                dispatched += 1

        recovery_result = SummaryRecoveryResult(
            failed_exhausted=len(failed_jobs),
            requeued_stale=len(requeued_jobs),
            dispatch_candidates=len(due_job_ids),
            dispatched=dispatched,
        )
        app_logger.info(
            (
                "摘要任务恢复扫描完成 failed_exhausted=%s requeued_stale=%s "
                "dispatch_candidates=%s dispatched=%s"
            ),
            recovery_result.failed_exhausted,
            recovery_result.requeued_stale,
            recovery_result.dispatch_candidates,
            recovery_result.dispatched,
        )
        return recovery_result

    async def _commit(self) -> None:
        """提交恢复扫描事务，失败时回滚当前 Session。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
