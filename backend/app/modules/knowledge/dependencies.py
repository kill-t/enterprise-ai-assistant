from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.session import get_db
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.object_storage.base import BaseObjectStorage
from app.infrastructure.object_storage.provider import get_object_storage
from app.infrastructure.task_queue.document_ingestion_dispatcher import (
    CeleryDocumentIngestionDispatcher,
)
from app.modules.knowledge.repository.document_ingestion_job_repository import (
    DocumentIngestionJobRepository,
)
from app.modules.knowledge.retrieval.evidence_selection import (
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.service.document_ingestion_dispatch_service import (
    DocumentIngestionDispatchService,
)
from app.modules.knowledge.service.document_service import DocumentService
from app.modules.knowledge.service.knowledge_base_service import KnowledgeBaseService
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


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
    job_repository = DocumentIngestionJobRepository(db)
    dispatch_service = DocumentIngestionDispatchService(
        db=db,
        job_repository=job_repository,
        dispatcher=CeleryDocumentIngestionDispatcher(),
        dispatch_lease_seconds=settings.worker.document_dispatch_lease_seconds,
    )
    return DocumentService(
        db=db,
        object_storage=object_storage,
        max_file_size_bytes=settings.object_storage.max_file_size_bytes,
        ingestion_dispatch_service=dispatch_service,
    )


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


def get_knowledge_search_service(
    db: AsyncSession = Depends(get_db),
) -> KnowledgeSearchService:
    """组装向量候选召回和当前无额度的阈值证据选择实现。"""
    return KnowledgeSearchService(
        db=db,
        embedding_factory=EmbeddingProviderFactory(),
        candidate_multiplier=settings.rag.search_candidate_multiplier,
        evidence_selector=ThresholdEvidenceSelector(),
    )


KnowledgeSearchServiceDep = Annotated[
    KnowledgeSearchService,
    Depends(get_knowledge_search_service),
]
