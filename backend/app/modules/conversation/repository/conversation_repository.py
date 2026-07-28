from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.schema.conversation_schema import ConversationCreate


class ConversationRepository:
    """封装会话实体的数据库读写，不负责提交事务。"""

    def __init__(self, db: AsyncSession):
        """保存当前请求使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationEntity:
        """创建会话实体并刷新数据库生成的字段。"""

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
        """按主键查询会话，不存在时返回 None。"""

        stmt = select(ConversationEntity).where(
            ConversationEntity.id == conversation_id
        )

        return await self.db.scalar(stmt)
