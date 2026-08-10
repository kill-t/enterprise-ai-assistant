"""集中导入会话模块实体，使 Alembic 能发现完整表结构。"""

from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.entity.conversation_knowledge_base import (
    ConversationKnowledgeBaseEntity,
)
from app.modules.conversation.entity.conversation_summary import (
    ConversationSummaryEntity,
)
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.entity.message_citation import MessageCitationEntity

__all__ = [
    "ConversationEntity",
    "ConversationKnowledgeBaseEntity",
    "ConversationSummaryEntity",
    "ConversationSummaryJobEntity",
    "MessageCitationEntity",
    "MessageEntity",
]
