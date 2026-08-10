"""组装会话模块新增能力的 FastAPI 依赖。"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import get_db
from app.modules.conversation.service.conversation_knowledge_base_service import (
    ConversationKnowledgeBaseService,
)


def get_conversation_knowledge_base_service(
    db: AsyncSession = Depends(get_db),
) -> ConversationKnowledgeBaseService:
    """为单次 HTTP 请求创建共享同一数据库 Session 的绑定 Service。"""
    return ConversationKnowledgeBaseService(db)


ConversationKnowledgeBaseServiceDep = Annotated[
    ConversationKnowledgeBaseService,
    Depends(get_conversation_knowledge_base_service),
]
