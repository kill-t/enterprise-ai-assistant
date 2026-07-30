from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.conversation_summary import (
    ConversationSummaryEntity,
)


class ConversationSummaryRepository:
    """封装会话滚动摘要的异步数据库读写，不控制事务提交。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存当前请求或任务使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        summary: ConversationSummaryEntity,
    ) -> ConversationSummaryEntity:
        """新增摘要并刷新数据库生成字段，但不提交事务。"""
        self.db.add(summary)
        await self.db.flush()
        await self.db.refresh(summary)
        return summary

    async def get_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> ConversationSummaryEntity | None:
        """按会话 ID 查询唯一滚动摘要，不存在时返回 None。"""
        statement = select(ConversationSummaryEntity).where(
            ConversationSummaryEntity.conversation_id == conversation_id
        )
        return await self.db.scalar(statement)

    async def update(
        self,
        summary: ConversationSummaryEntity,
    ) -> ConversationSummaryEntity:
        """刷新上层已更新的摘要正文、覆盖游标和版本，不隐式提交。"""
        await self.db.flush()
        await self.db.refresh(summary)
        return summary
