from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import get_db
from app.modules.knowledge.service.knowledge_base_service import KnowledgeBaseService


def get_knowledge_base_service(
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseService:
    """为一次 HTTP 请求组装共用数据库 Session 的知识库服务。"""
    return KnowledgeBaseService(db)


KnowledgeBaseServiceDep = Annotated[
    KnowledgeBaseService,
    Depends(get_knowledge_base_service),
]
