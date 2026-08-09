from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.session import get_db
from app.infrastructure.object_storage.base import BaseObjectStorage
from app.infrastructure.object_storage.provider import get_object_storage
from app.modules.knowledge.service.document_service import DocumentService
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


def get_document_service(
    db: AsyncSession = Depends(get_db),
    object_storage: BaseObjectStorage = Depends(get_object_storage),
) -> DocumentService:
    """组装文档服务及其数据库、对象存储和上传大小配置。"""
    return DocumentService(
        db=db,
        object_storage=object_storage,
        max_file_size_bytes=settings.object_storage.max_file_size_bytes,
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
