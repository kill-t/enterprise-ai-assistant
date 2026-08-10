"""会话绑定知识库检索的内部结构化结果。"""

from uuid import UUID

from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchResponse,
)


class ConversationKnowledgeSearchResponse(MultiKnowledgeSearchResponse):
    """补充会话身份和被跳过的归档绑定，方便后续聊天审计。"""

    conversation_id: UUID
    skipped_archived_knowledge_base_ids: list[UUID]
