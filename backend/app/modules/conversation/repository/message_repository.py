from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import MessageStatus
from app.modules.conversation.entity.message import MessageEntity


class MessageRepository:
    """封装消息实体的持久化和历史查询。"""

    def __init__(self, db: AsyncSession):
        """保存当前请求使用的异步数据库 Session。"""
        self.db = db

    async def create(self, message: MessageEntity) -> MessageEntity:
        """新增消息并刷新数据库生成的字段，不提交事务。"""
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def update(self, message: MessageEntity) -> MessageEntity:
        """刷新已修改消息，使内存状态与数据库保持一致。"""
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def get_by_id(self, message_id: UUID) -> MessageEntity | None:
        """按主键查询消息，不存在时返回 None。"""
        stmt = select(MessageEntity).where(MessageEntity.id == message_id)
        return await self.db.scalar(stmt)

    async def list_by_conversation(
        self,
        conversation_id: UUID,
    ) -> list[MessageEntity]:
        """按时间顺序返回会话的全部消息，包括失败和处理中消息。"""
        stmt = (
            select(MessageEntity)
            .where(MessageEntity.conversation_id == conversation_id)
            .order_by(MessageEntity.created_at)
        )
        return list((await self.db.scalars(stmt)).all())

    async def list_completed_by_conversation(
        self,
        conversation_id: UUID,
    ) -> list[MessageEntity]:
        """只返回已完成消息，供模型上下文和 Redis 回填使用。"""
        stmt = (
            select(MessageEntity)
            .where(
                MessageEntity.conversation_id == conversation_id,
                MessageEntity.status == MessageStatus.COMPLETED,
            )
            .order_by(MessageEntity.created_at)
        )
        return list((await self.db.scalars(stmt)).all())
