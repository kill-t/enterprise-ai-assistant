from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
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
            .order_by(MessageEntity.created_at, MessageEntity.id)
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
            .order_by(MessageEntity.created_at, MessageEntity.id)
        )
        return list((await self.db.scalars(stmt)).all())

    async def list_completed_after_cursor(
        self,
        conversation_id: UUID,
        covered_until_created_at: datetime,
        covered_until_message_id: UUID | None,
    ) -> list[MessageEntity]:
        """按稳定时间顺序返回摘要游标之后的已完成消息。"""
        if covered_until_message_id is None:
            # 游标消息被删除时使用 >=，宁可重复同一时刻消息，也不能静默遗漏。
            cursor_condition = MessageEntity.created_at >= covered_until_created_at
        else:
            cursor_condition = or_(
                MessageEntity.created_at > covered_until_created_at,
                and_(
                    MessageEntity.created_at == covered_until_created_at,
                    MessageEntity.id > covered_until_message_id,
                ),
            )

        statement = (
            select(MessageEntity)
            .where(
                MessageEntity.conversation_id == conversation_id,
                MessageEntity.status == MessageStatus.COMPLETED,
                cursor_condition,
            )
            .order_by(MessageEntity.created_at, MessageEntity.id)
        )
        return list((await self.db.scalars(statement)).all())

    async def list_completed_in_summary_range(
        self,
        conversation_id: UUID,
        through_created_at: datetime,
        through_message_id: UUID,
        after_created_at: datetime | None = None,
        after_message_id: UUID | None = None,
    ) -> list[MessageEntity]:
        """读取旧摘要游标之后、任务冻结目标之前的已完成消息。"""
        conditions = [
            MessageEntity.conversation_id == conversation_id,
            MessageEntity.status == MessageStatus.COMPLETED,
            or_(
                MessageEntity.created_at < through_created_at,
                and_(
                    MessageEntity.created_at == through_created_at,
                    MessageEntity.id <= through_message_id,
                ),
            ),
        ]

        if after_created_at is not None:
            if after_message_id is None:
                # 旧游标消息被删除时宁可重复同一时刻的数据，也不能静默遗漏。
                lower_bound = MessageEntity.created_at >= after_created_at
            else:
                lower_bound = or_(
                    MessageEntity.created_at > after_created_at,
                    and_(
                        MessageEntity.created_at == after_created_at,
                        MessageEntity.id > after_message_id,
                    ),
                )
            conditions.append(lower_bound)

        statement = (
            select(MessageEntity)
            .where(*conditions)
            .order_by(MessageEntity.created_at, MessageEntity.id)
        )
        return list((await self.db.scalars(statement)).all())
