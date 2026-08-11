from uuid import UUID

from sqlalchemy import delete, select, update
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

    async def retry_failed_with_job(
        self,
        document_id: UUID,
        job_id: UUID,
    ) -> tuple[DocumentEntity, DocumentIngestionJobEntity] | None:
        """原子把 FAILED 文档恢复为 PENDING，并新建一条独立任务历史。"""
        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.FAILED,
            )
            .values(
                status=DocumentStatus.PENDING,
                parser_version=None,
                chunker_version=None,
                page_count=0,
                chunk_count=0,
                error_code=None,
                error_message=None,
                finished_at=None,
            )
            .returning(DocumentEntity)
        )
        document = await self.db.scalar(statement)
        if document is None:
            return None

        job = DocumentIngestionJobEntity(
            id=job_id,
            document_id=document_id,
            idempotency_key=(
                f"document-ingestion:{document_id}:manual:{job_id}"
            ),
            status=DocumentIngestionJobStatus.PENDING,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(document)
        await self.db.refresh(job)
        return document, job

    async def get_by_id_for_user_including_deleted(
        self,
        document_id: UUID,
        user_id: UUID,
    ) -> DocumentEntity | None:
        """删除恢复流程需要读取 DELETED 墓碑，同时继续执行用户所有权过滤。"""
        statement = (
            select(DocumentEntity)
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                DocumentEntity.id == document_id,
                KnowledgeBaseEntity.user_id == user_id,
            )
        )
        return await self.db.scalar(statement)

    async def mark_deleted_if_terminal(
        self,
        document_id: UUID,
    ) -> DocumentEntity | None:
        """只允许 READY 或 FAILED 文档进入逻辑删除，避免与活动 Worker 竞争。"""
        statement = (
            update(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status.in_(
                    [DocumentStatus.READY, DocumentStatus.FAILED]
                ),
            )
            .values(status=DocumentStatus.DELETED)
            .returning(DocumentEntity)
        )
        return await self.db.scalar(statement)

    async def hard_delete_if_marked(self, document_id: UUID) -> UUID | None:
        """对象清理成功后删除 DELETED 墓碑，并由外键级联清理任务和 Chunk。"""
        statement = (
            delete(DocumentEntity)
            .where(
                DocumentEntity.id == document_id,
                DocumentEntity.status == DocumentStatus.DELETED,
            )
            .returning(DocumentEntity.id)
        )
        return await self.db.scalar(statement)
