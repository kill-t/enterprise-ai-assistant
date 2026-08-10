"""编排会话知识库选择的所有权校验和原子替换事务。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.repository.conversation_knowledge_base_repository import (
    ConversationKnowledgeBaseRepository,
)
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.schema.conversation_knowledge_base_schema import (
    ConversationKnowledgeBaseSelectionResponse,
    ConversationKnowledgeBaseUpdate,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.schema.knowledge_base_schema import KnowledgeBaseResponse


class ConversationKnowledgeBaseService:
    """保证一个会话只能选择当前用户拥有且可用的知识库。"""

    def __init__(self, db: AsyncSession) -> None:
        """组装会话、知识库和绑定 Repository，并保留事务控制权。"""
        self.db = db
        self.conversation_repository = ConversationRepository(db)
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.binding_repository = ConversationKnowledgeBaseRepository(db)

    async def get_selection(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> ConversationKnowledgeBaseSelectionResponse:
        """校验会话所有权后返回当前绑定，包括后来被归档的知识库。"""
        await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        knowledge_bases = await self.binding_repository.list_for_conversation_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        return self._to_response(
            conversation_id=conversation_id,
            knowledge_bases=knowledge_bases,
        )

    async def replace_selection(
        self,
        user_id: UUID,
        conversation_id: UUID,
        request: ConversationKnowledgeBaseUpdate,
    ) -> ConversationKnowledgeBaseSelectionResponse:
        """完整校验目标集合后，在一个事务中删除旧绑定并写入新绑定。"""
        await self._get_owned_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        knowledge_bases = await self._get_bindable_knowledge_bases(
            user_id=user_id,
            knowledge_base_ids=request.knowledge_base_ids,
        )
        try:
            await self.binding_repository.replace(
                conversation_id=conversation_id,
                knowledge_base_ids=request.knowledge_base_ids,
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        app_logger.info(
            "会话知识库绑定已替换 user_id=%s conversation_id=%s count=%s",
            user_id,
            conversation_id,
            len(knowledge_bases),
        )
        return self._to_response(
            conversation_id=conversation_id,
            knowledge_bases=knowledge_bases,
        )

    async def _get_owned_conversation(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> ConversationEntity:
        """不存在和跨用户会话统一返回 404，避免泄露资源存在性。"""
        conversation = await self.conversation_repository.get_by_id_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise BusinessException(
                code=ErrorCode.CONVERSATION_NOT_FOUND,
                message="会话不存在",
                status_code=404,
            )
        return conversation

    async def _get_bindable_knowledge_bases(
        self,
        user_id: UUID,
        knowledge_base_ids: list[UUID],
    ) -> list[KnowledgeBaseEntity]:
        """批量锁定并验证完整目标集合，再恢复客户端提交顺序。"""
        if not knowledge_base_ids:
            return []
        entities = await self.knowledge_base_repository.list_by_ids_for_user(
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            for_update=True,
        )
        entities_by_id = {entity.id: entity for entity in entities}
        if set(entities_by_id) != set(knowledge_base_ids):
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                status_code=404,
            )

        ordered_entities = [
            entities_by_id[knowledge_base_id]
            for knowledge_base_id in knowledge_base_ids
        ]
        if any(entity.is_archived for entity in ordered_entities):
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_ARCHIVED,
                message="已归档知识库不能绑定到会话",
                status_code=409,
            )
        return ordered_entities

    @staticmethod
    def _to_response(
        conversation_id: UUID,
        knowledge_bases: list[KnowledgeBaseEntity],
    ) -> ConversationKnowledgeBaseSelectionResponse:
        """把 ORM 实体转换为稳定的会话知识库选择响应。"""
        return ConversationKnowledgeBaseSelectionResponse(
            conversation_id=conversation_id,
            knowledge_bases=[
                KnowledgeBaseResponse.model_validate(entity)
                for entity in knowledge_bases
            ],
        )
