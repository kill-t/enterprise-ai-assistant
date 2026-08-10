"""封装会话知识库绑定的读取和整组替换 SQL。"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.entity.conversation_knowledge_base import (
    ConversationKnowledgeBaseEntity,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity


class ConversationKnowledgeBaseRepository:
    """访问显式绑定表，但不判断所有权业务规则或提交事务。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存当前 HTTP 请求共用的异步数据库 Session。"""
        self.db = db

    async def list_for_conversation_user(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[KnowledgeBaseEntity]:
        """读取绑定并在 SQL 层同时过滤会话和知识库所有者。"""
        statement = (
            select(KnowledgeBaseEntity)
            .join(
                ConversationKnowledgeBaseEntity,
                ConversationKnowledgeBaseEntity.knowledge_base_id
                == KnowledgeBaseEntity.id,
            )
            .join(
                ConversationEntity,
                ConversationEntity.id
                == ConversationKnowledgeBaseEntity.conversation_id,
            )
            .where(
                ConversationEntity.id == conversation_id,
                ConversationEntity.user_id == user_id,
                KnowledgeBaseEntity.user_id == user_id,
            )
            .order_by(KnowledgeBaseEntity.name.asc(), KnowledgeBaseEntity.id.asc())
        )
        return list((await self.db.scalars(statement)).all())

    async def replace(
        self,
        conversation_id: UUID,
        knowledge_base_ids: list[UUID],
    ) -> None:
        """在当前事务中删除旧绑定并暂存整组新绑定，不自行提交。"""
        await self.db.execute(
            delete(ConversationKnowledgeBaseEntity).where(
                ConversationKnowledgeBaseEntity.conversation_id == conversation_id
            )
        )
        if knowledge_base_ids:
            self.db.add_all(
                [
                    ConversationKnowledgeBaseEntity(
                        conversation_id=conversation_id,
                        knowledge_base_id=knowledge_base_id,
                    )
                    for knowledge_base_id in knowledge_base_ids
                ]
            )
        await self.db.flush()
