"""集中导入知识模块实体，使 Alembic 能发现完整表结构。"""

from app.modules.knowledge.entity.document import DocumentEntity
from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity
from app.modules.knowledge.entity.document_ingestion_job import (
    DocumentIngestionJobEntity,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity

__all__ = [
    "DocumentChunkEntity",
    "DocumentEntity",
    "DocumentIngestionJobEntity",
    "KnowledgeBaseEntity",
]
