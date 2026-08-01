from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.loggers import app_logger
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.service.conversation_summary_processor import (
    GeneratedConversationSummary,
    PreparedConversationSummary,
)


class ConversationSummaryProcessor(Protocol):
    """定义摘要处理器的读取、生成和持久化三阶段接口。"""

    async def prepare(
        self,
        job: ConversationSummaryJobEntity,
    ) -> PreparedConversationSummary:
        """读取并冻结模型输入。"""
        ...

    async def generate(
        self,
        prepared: PreparedConversationSummary,
    ) -> GeneratedConversationSummary:
        """在无数据库事务期间生成摘要。"""
        ...

    async def persist(
        self,
        prepared: PreparedConversationSummary,
        generated: GeneratedConversationSummary,
    ) -> None:
        """暂存摘要修改，等待 Worker 与成功状态一起提交。"""
        ...


class ConversationSummaryWorkerService:
    """编排摘要任务的领取、执行和持久化收尾，不依赖 Celery API。"""

    def __init__(
        self,
        db: AsyncSession,
        job_repository: ConversationSummaryJobRepository,
        processor: ConversationSummaryProcessor,
    ) -> None:
        """注入数据库、任务仓库和可替换的业务处理器。"""
        self.db = db
        self.job_repository = job_repository
        self.processor = processor

    async def execute(self, job_id: UUID) -> str:
        """原子领取并执行任务，返回便于日志和测试判断的稳定结果。"""
        claimed_job = await self.job_repository.claim_pending(
            job_id=job_id,
            started_at=datetime.now(timezone.utc),
        )
        # 领取结果必须先提交，其他 Worker 才能看见 RUNNING 状态并跳过重复消息。
        await self._commit()

        if claimed_job is None:
            app_logger.info("摘要任务未取得执行权，跳过本次消息 job_id=%s", job_id)
            return "skipped"

        attempts = claimed_job.attempts
        app_logger.info(
            "摘要任务已取得执行权 job_id=%s attempts=%s",
            job_id,
            attempts,
        )

        try:
            prepared = await self.processor.prepare(claimed_job)
            # 输入已经复制到普通数据结构，释放只读事务后再等待耗时的模型调用。
            await self._commit()
            generated = await self.processor.generate(prepared)
            await self.processor.persist(prepared, generated)
        except Exception as exc:
            # 完整异常只进入日志；数据库保存稳定错误码和安全提示，避免泄露内部信息。
            app_logger.error(
                "摘要任务处理异常 job_id=%s attempts=%s reason=%s",
                job_id,
                attempts,
                type(exc).__name__,
                exc_info=True,
            )
            # 真实处理器以后会在同一 Session 中写摘要；失败时先清除所有半成品修改。
            await self.db.rollback()
            failed_job = await self.job_repository.mark_failed(
                job_id=job_id,
                expected_attempts=attempts,
                finished_at=datetime.now(timezone.utc),
                error_code="SUMMARY_PROCESSING_FAILED",
                error_message="会话摘要任务处理失败",
            )
            await self._commit()
            if failed_job is None:
                app_logger.warning(
                    "摘要任务失败结果已过期，未覆盖较新状态 job_id=%s attempts=%s",
                    job_id,
                    attempts,
                )
                return "stale"
            return "failed"

        succeeded_job = await self.job_repository.mark_succeeded(
            job_id=job_id,
            expected_attempts=attempts,
            finished_at=datetime.now(timezone.utc),
        )
        if succeeded_job is None:
            # Processor 的摘要修改尚未提交；执行权已过期时必须整体回滚。
            await self.db.rollback()
            app_logger.warning(
                "摘要任务成功结果已过期，未覆盖较新状态 job_id=%s attempts=%s",
                job_id,
                attempts,
            )
            return "stale"

        # 摘要正文、覆盖游标和 SUCCEEDED 状态在同一事务中落库。
        await self._commit()
        app_logger.info(
            "摘要任务处理成功 job_id=%s attempts=%s",
            job_id,
            attempts,
        )
        return "succeeded"

    async def _commit(self) -> None:
        """提交当前阶段的事务，提交失败时回滚 Session 后继续抛出异常。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
