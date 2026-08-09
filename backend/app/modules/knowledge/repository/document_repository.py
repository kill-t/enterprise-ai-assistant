from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentIngestionJobStatus, DocumentStatus
from app.modules.knowledge.models import (
    DocumentEntity,
    DocumentIngestionJobEntity,
    KnowledgeBaseEntity,
)


class DocumentRepository:
    """封装文档和入库任务的数据访问，不在 Repository 中提交事务。"""

    def __init__(self, db: AsyncSession):
        """保存当前请求共用的异步数据库 Session。"""
        self.db = db

    async def get_by_sha256_for_knowledge_base(
        self,
        knowledge_base_id: UUID,
        sha256: str,
    ) -> DocumentEntity | None:
        """查找同一知识库中内容哈希完全相同的文档。"""
        statement = select(DocumentEntity).where(
            DocumentEntity.knowledge_base_id == knowledge_base_id,
            DocumentEntity.sha256 == sha256,
        )
        return await self.db.scalar(statement)

    async def create_with_job(
        self,
        document_id: UUID,
        job_id: UUID,
        knowledge_base_id: UUID,
        filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        object_key: str,
    ) -> tuple[DocumentEntity, DocumentIngestionJobEntity]:
        """在当前事务中同时创建文档和对应的 PENDING 入库任务。"""
        document = DocumentEntity(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            object_key=object_key,
            status=DocumentStatus.PENDING,
        )
        job = DocumentIngestionJobEntity(
            id=job_id,
            document_id=document_id,
            idempotency_key=f"document-ingestion:{document_id}:v1",
            status=DocumentIngestionJobStatus.PENDING,
        )
        self.db.add_all([document, job])
        await self.db.flush()
        await self.db.refresh(document)
        await self.db.refresh(job)
        return document, job

    async def list_by_knowledge_base_for_user(
        self,
        knowledge_base_id: UUID,
        user_id: UUID,
    ) -> list[DocumentEntity]:
        """按所有权过滤并返回知识库中未删除文档的状态列表。"""
        statement = (
            select(DocumentEntity)
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                DocumentEntity.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseEntity.user_id == user_id,
                DocumentEntity.status != DocumentStatus.DELETED,
            )
            .order_by(DocumentEntity.created_at.desc())
        )
        return list((await self.db.scalars(statement)).all())

    async def get_by_id_for_user(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> DocumentEntity | None:
        """通过所属知识库过滤当前用户可访问的指定文档。"""
        statement = (
            select(DocumentEntity)
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                DocumentEntity.id == document_id,
                KnowledgeBaseEntity.user_id == user_id,
                DocumentEntity.status != DocumentStatus.DELETED,
            )
        )
        return await self.db.scalar(statement)
