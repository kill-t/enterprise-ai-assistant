from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.schema.conversation_schema import ConversationCreate


class ConversationRepository:
    """封装带用户所有权边界的会话数据库读写，不负责提交事务。"""

    def __init__(self, db: AsyncSession):
        """保存当前请求使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationEntity:
        """为指定用户创建会话并刷新数据库生成字段。"""
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

    async def get_by_id_for_user(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> ConversationEntity | None:
        """同时按会话主键和所有者查询，避免仅凭 UUID 跨用户访问。"""
        stmt = select(ConversationEntity).where(
            ConversationEntity.id == conversation_id,
            ConversationEntity.user_id == user_id,
        )
        return await self.db.scalar(stmt)

    async def list_by_user(
        self,
        user_id: UUID,
        include_archived: bool = False,
    ) -> list[ConversationEntity]:
        """按最近更新时间返回用户会话，并默认隐藏已归档记录。"""
        stmt = select(ConversationEntity).where(
            ConversationEntity.user_id == user_id
        )
        if not include_archived:
            stmt = stmt.where(ConversationEntity.is_archived.is_(False))
        stmt = stmt.order_by(ConversationEntity.updated_at.desc())
        return list((await self.db.scalars(stmt)).all())

    async def update(self, conversation: ConversationEntity) -> ConversationEntity:
        """刷新已修改会话，不提交上层业务事务。"""
        await self.db.flush()
        await self.db.refresh(conversation)
        return conversation

    async def delete(self, conversation: ConversationEntity) -> None:
        """标记删除指定会话，并通过 ORM 级联删除其消息。"""
        await self.db.delete(conversation)
        await self.db.flush()
