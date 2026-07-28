from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.schema.conversation_schema import ConversationCreate


class ConversationRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationEntity:

        entity = ConversationEntity(
            user_id=user_id,
            title=conversation.title,
            system_prompt=conversation.system_prompt,
            model_name=conversation.model_name,
            temperature=conversation.temperature,
        )

        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)

        return entity

    async def get_by_id(
        self,
        conversation_id: UUID,
    ) -> ConversationEntity | None:

        stmt = select(ConversationEntity).where(
            ConversationEntity.id == conversation_id
        )

        return await self.db.scalar(stmt)
