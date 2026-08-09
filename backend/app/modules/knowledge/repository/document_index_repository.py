from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.infrastructure.embedding.schemas import EmbeddingProfile
from app.modules.knowledge.entity.document import DocumentEntity
from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.processing.ingestion_schemas import (
    DocumentIngestionSource,
    ProcessedDocumentIngestion,
)


class DocumentIndexRepository:
    """封装 Worker 对文档来源、状态和 Chunk 索引的数据访问。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存 Worker 生命周期内共用的异步 Session。"""
        self.db = db

    async def mark_processing(
        self,
        document_id: UUID,
        started_at: datetime,
    ) -> UUID | None:
        """把等待或重试中的文档切换为 PROCESSING，并清理旧错误。"""
        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.PENDING,
            )
            .values(
                status=DocumentStatus.PROCESSING,
                error_code=None,
                error_message=None,
                finished_at=None,
                updated_at=started_at,
            )
            .returning(DocumentEntity.id)
        )
        return await self.db.scalar(statement)

    async def get_processing_source(
        self,
        document_id: UUID,
    ) -> DocumentIngestionSource | None:
        """读取原文件定位和知识库 Embedding 快照，并复制成普通数据结构。"""
        statement = (
            select(
                DocumentEntity.id.label("document_id"),
                DocumentEntity.filename,
                DocumentEntity.object_key,
                DocumentEntity.size_bytes,
                DocumentEntity.sha256,
                KnowledgeBaseEntity.embedding_provider,
                KnowledgeBaseEntity.embedding_model,
                KnowledgeBaseEntity.embedding_dimension,
            )
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.PROCESSING,
            )
        )
        row = (await self.db.execute(statement)).one_or_none()
        if row is None:
            return None
        return DocumentIngestionSource(
            document_id=row.document_id,
            filename=row.filename,
            object_key=row.object_key,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            embedding_profile=EmbeddingProfile(
                provider=row.embedding_provider,
                model=row.embedding_model,
                dimension=row.embedding_dimension,
            ),
        )

    async def stage_success(
        self,
        source: DocumentIngestionSource,
        processed: ProcessedDocumentIngestion,
        finished_at: datetime,
    ) -> UUID | None:
        """暂存完整 Chunk 替换与 READY 状态，最终由 Worker 统一提交。"""
        await self.db.execute(
            delete(DocumentChunkEntity).where(
                DocumentChunkEntity.document_id == source.document_id
            )
        )
        chunk_entities = [
            DocumentChunkEntity(
                document_id=source.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                heading_path=chunk.heading_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                token_count=chunk.token_count,
                embedding_model=processed.embedding_model,
                embedding=list(vector),
            )
            for chunk, vector in zip(
                processed.chunks,
                processed.vectors,
                strict=True,
            )
        ]
        self.db.add_all(chunk_entities)

        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == source.document_id,
                DocumentEntity.status == DocumentStatus.PROCESSING,
            )
            .values(
                status=DocumentStatus.READY,
                parser_version=processed.parser_version,
                chunker_version=processed.chunker_version,
                page_count=processed.page_count,
                chunk_count=len(processed.chunks),
                error_code=None,
                error_message=None,
                finished_at=finished_at,
                updated_at=finished_at,
            )
            .returning(DocumentEntity.id)
        )
        return await self.db.scalar(statement)

    async def mark_retry_pending(
        self,
        document_id: UUID,
        error_code: str,
        error_message: str,
        updated_at: datetime,
    ) -> UUID | None:
        """把临时失败文档恢复为 PENDING，保留安全错误供前端观察。"""
        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.PROCESSING,
            )
            .values(
                status=DocumentStatus.PENDING,
                error_code=error_code,
                error_message=error_message,
                finished_at=None,
                updated_at=updated_at,
            )
            .returning(DocumentEntity.id)
        )
        return await self.db.scalar(statement)

    async def mark_failed(
        self,
        document_id: UUID,
        error_code: str,
        error_message: str,
        finished_at: datetime,
    ) -> UUID | None:
        """把不可恢复错误同步保存到文档终态。"""
        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.PROCESSING,
            )
            .values(
                status=DocumentStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                finished_at=finished_at,
                updated_at=finished_at,
            )
            .returning(DocumentEntity.id)
        )
        return await self.db.scalar(statement)
