from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import SummaryJobStatus
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)


class ConversationSummaryJobRepository:
    """封装摘要任务状态的异步数据库访问，不控制事务提交。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存任务 Service 使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        job: ConversationSummaryJobEntity,
    ) -> ConversationSummaryJobEntity:
        """新增持久化任务并刷新生成字段，但不提交事务。"""
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def get_by_id(
        self,
        job_id: UUID,
    ) -> ConversationSummaryJobEntity | None:
        """按任务 ID 查询状态，不存在时返回 None。"""
        statement = select(ConversationSummaryJobEntity).where(
            ConversationSummaryJobEntity.id == job_id
        )
        return await self.db.scalar(statement)

    async def get_active_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> ConversationSummaryJobEntity | None:
        """查询会话当前待处理或运行中的唯一摘要任务。"""
        statement = select(ConversationSummaryJobEntity).where(
            ConversationSummaryJobEntity.conversation_id == conversation_id,
            ConversationSummaryJobEntity.status.in_(
                (SummaryJobStatus.PENDING, SummaryJobStatus.RUNNING)
            ),
        )
        return await self.db.scalar(statement)

    async def update(
        self,
        job: ConversationSummaryJobEntity,
    ) -> ConversationSummaryJobEntity:
        """刷新上层已修改的任务状态，不隐式提交事务。"""
        await self.db.flush()
        await self.db.refresh(job)
        return job
