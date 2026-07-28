from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import MessageStatus
from app.modules.conversation.entity.message import MessageEntity


class MessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, message: MessageEntity) -> MessageEntity:
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def update(self, message: MessageEntity) -> MessageEntity:
        await self.db.flush()
        await self.db.refresh(message)
        return message

    async def get_by_id(self, message_id: UUID) -> MessageEntity | None:
        stmt = select(MessageEntity).where(MessageEntity.id == message_id)
        return await self.db.scalar(stmt)

    async def list_by_conversation(
        self,
        conversation_id: UUID,
    ) -> list[MessageEntity]:
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
        stmt = (
            select(MessageEntity)
            .where(
                MessageEntity.conversation_id == conversation_id,
                MessageEntity.status == MessageStatus.COMPLETED,
            )
            .order_by(MessageEntity.created_at)
        )
        return list((await self.db.scalars(stmt)).all())
