"""把会话知识库绑定适配为核心多知识库检索输入。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.modules.conversation.repository.conversation_knowledge_base_repository import (
    ConversationKnowledgeBaseRepository,
)
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.schema.conversation_knowledge_search_schema import (
    ConversationKnowledgeSearchResponse,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
)
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)
from app.modules.knowledge.service.knowledge_search_factory import (
    create_knowledge_search_service,
)


class ConversationKnowledgeSearchService:
    """验证会话所有权，跳过归档绑定，再复用核心跨库检索能力。"""

    def __init__(
        self,
        db: AsyncSession,
        knowledge_search_service: KnowledgeSearchService | None = None,
    ) -> None:
        """组装会话、绑定 Repository 和可复用知识检索 Service。"""
        self.conversation_repository = ConversationRepository(db)
        self.binding_repository = ConversationKnowledgeBaseRepository(db)
        self.knowledge_search_service = (
            knowledge_search_service
            or create_knowledge_search_service(db=db)
        )

    async def search(
        self,
        user_id: UUID,
        conversation_id: UUID,
        request: KnowledgeSearchRequest,
    ) -> ConversationKnowledgeSearchResponse:
        """读取当前会话活动绑定，并返回包含跳过状态的统一证据。"""
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

        bound_knowledge_bases = (
            await self.binding_repository.list_for_conversation_user(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        )
        active_knowledge_bases = [
            item for item in bound_knowledge_bases if not item.is_archived
        ]
        skipped_archived_ids = [
            item.id for item in bound_knowledge_bases if item.is_archived
        ]
        result = await self.knowledge_search_service.search_many(
            user_id=user_id,
            knowledge_bases=active_knowledge_bases,
            request=request,
        )

        app_logger.info(
            "会话知识检索完成 user_id=%s conversation_id=%s searched=%s skipped_archived=%s evidence=%s",
            user_id,
            conversation_id,
            len(result.searched_knowledge_base_ids),
            len(skipped_archived_ids),
            len(result.results),
        )
        return ConversationKnowledgeSearchResponse(
            conversation_id=conversation_id,
            query=result.query,
            searched_knowledge_base_ids=result.searched_knowledge_base_ids,
            skipped_archived_knowledge_base_ids=skipped_archived_ids,
            similarity_threshold=result.similarity_threshold,
            results=result.results,
        )
