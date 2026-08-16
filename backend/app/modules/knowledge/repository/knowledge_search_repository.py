"""封装知识库 Chunk 的 pgvector 相似度检索 SQL。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus
from app.common.enums import SourceType
from app.modules.knowledge.entity.document import DocumentEntity
from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.search_models import (
    KnowledgeSearchCandidate,
    MultiKnowledgeSearchCandidate,
)
from app.modules.knowledge.source_locator import parse_source_locator


class KnowledgeSearchRepository:
    """只负责安全构造和执行向量查询，不决定可靠性业务阈值。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存当前请求生命周期内共用的异步 Session。"""
        self.db = db

    async def has_incompatible_ready_chunks(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        embedding_model: str,
    ) -> bool:
        """检查 READY 文档是否混入与知识库快照不一致的模型向量。"""
        statement = (
            select(DocumentChunkEntity.id)
            .join(
                DocumentEntity,
                DocumentEntity.id == DocumentChunkEntity.document_id,
            )
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                KnowledgeBaseEntity.id == knowledge_base_id,
                KnowledgeBaseEntity.user_id == user_id,
                KnowledgeBaseEntity.is_archived.is_(False),
                DocumentEntity.status == DocumentStatus.READY,
                DocumentChunkEntity.embedding_model != embedding_model,
            )
            .limit(1)
        )
        return await self.db.scalar(statement) is not None

    async def search_candidates(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        query_vector: Sequence[float],
        embedding_model: str,
        candidate_limit: int,
    ) -> list[KnowledgeSearchCandidate]:
        """按余弦距离召回候选，同时在 SQL 层执行所有权和文档状态过滤。"""
        cosine_distance = DocumentChunkEntity.embedding.cosine_distance(
            list(query_vector)
        )
        similarity = (1.0 - cosine_distance).label("score")
        statement = (
            select(
                DocumentChunkEntity.id.label("chunk_id"),
                DocumentChunkEntity.document_id,
                DocumentEntity.filename,
                DocumentChunkEntity.chunk_index,
                DocumentChunkEntity.content,
                DocumentChunkEntity.heading_path,
                DocumentChunkEntity.page_start,
                DocumentChunkEntity.page_end,
                DocumentChunkEntity.source_type,
                DocumentChunkEntity.source_locator,
                DocumentChunkEntity.token_count,
                similarity,
            )
            .join(
                DocumentEntity,
                DocumentEntity.id == DocumentChunkEntity.document_id,
            )
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                KnowledgeBaseEntity.id == knowledge_base_id,
                KnowledgeBaseEntity.user_id == user_id,
                KnowledgeBaseEntity.is_archived.is_(False),
                DocumentEntity.status == DocumentStatus.READY,
                DocumentChunkEntity.embedding_model == embedding_model,
            )
            # pgvector 索引要求直接按距离运算符升序并配合 LIMIT，避免附加排序破坏索引路径。
            .order_by(cosine_distance.asc())
            .limit(candidate_limit)
        )
        rows = (await self.db.execute(statement)).all()
        return [
            KnowledgeSearchCandidate(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                filename=row.filename,
                chunk_index=row.chunk_index,
                content=row.content,
                heading_path=row.heading_path,
                page_start=row.page_start,
                page_end=row.page_end,
                source_type=SourceType(row.source_type),
                source_locator=parse_source_locator(row.source_locator),
                token_count=row.token_count,
                # 浮点计算可能产生极小越界，响应前收敛到余弦相似度合法区间。
                score=max(-1.0, min(1.0, float(row.score))),
            )
            for row in rows
        ]

    async def list_incompatible_ready_knowledge_base_ids(
        self,
        user_id: UUID,
        knowledge_base_ids: list[UUID],
    ) -> list[UUID]:
        """一次找出选中知识库内与各自配置快照不一致的 READY Chunk。"""
        if not knowledge_base_ids:
            return []
        statement = (
            select(KnowledgeBaseEntity.id)
            .join(
                DocumentEntity,
                DocumentEntity.knowledge_base_id == KnowledgeBaseEntity.id,
            )
            .join(
                DocumentChunkEntity,
                DocumentChunkEntity.document_id == DocumentEntity.id,
            )
            .where(
                KnowledgeBaseEntity.id.in_(knowledge_base_ids),
                KnowledgeBaseEntity.user_id == user_id,
                KnowledgeBaseEntity.is_archived.is_(False),
                DocumentEntity.status == DocumentStatus.READY,
                DocumentChunkEntity.embedding_model
                != KnowledgeBaseEntity.embedding_model,
            )
            .distinct()
            .order_by(KnowledgeBaseEntity.id.asc())
        )
        return list((await self.db.scalars(statement)).all())

    async def search_candidates_across_knowledge_bases(
        self,
        user_id: UUID,
        knowledge_base_ids: list[UUID],
        query_vector: Sequence[float],
        embedding_model: str,
        candidate_limit: int,
    ) -> list[MultiKnowledgeSearchCandidate]:
        """对一组同模型知识库执行一次向量查询，并返回全局候选。"""
        if not knowledge_base_ids:
            return []
        cosine_distance = DocumentChunkEntity.embedding.cosine_distance(
            list(query_vector)
        )
        similarity = (1.0 - cosine_distance).label("score")
        statement = (
            select(
                KnowledgeBaseEntity.id.label("knowledge_base_id"),
                KnowledgeBaseEntity.name.label("knowledge_base_name"),
                DocumentChunkEntity.id.label("chunk_id"),
                DocumentChunkEntity.document_id,
                DocumentEntity.filename,
                DocumentChunkEntity.chunk_index,
                DocumentChunkEntity.content,
                DocumentChunkEntity.content_hash,
                DocumentChunkEntity.heading_path,
                DocumentChunkEntity.page_start,
                DocumentChunkEntity.page_end,
                DocumentChunkEntity.source_type,
                DocumentChunkEntity.source_locator,
                DocumentChunkEntity.token_count,
                similarity,
            )
            .join(
                DocumentEntity,
                DocumentEntity.id == DocumentChunkEntity.document_id,
            )
            .join(
                KnowledgeBaseEntity,
                KnowledgeBaseEntity.id == DocumentEntity.knowledge_base_id,
            )
            .where(
                KnowledgeBaseEntity.id.in_(knowledge_base_ids),
                KnowledgeBaseEntity.user_id == user_id,
                KnowledgeBaseEntity.is_archived.is_(False),
                KnowledgeBaseEntity.embedding_model == embedding_model,
                DocumentEntity.status == DocumentStatus.READY,
                DocumentChunkEntity.embedding_model == embedding_model,
            )
            # 一个距离排序和 LIMIT 作用于全部选中知识库，得到真正的全局候选集合。
            .order_by(cosine_distance.asc())
            .limit(candidate_limit)
        )
        rows = (await self.db.execute(statement)).all()
        return [
            MultiKnowledgeSearchCandidate(
                knowledge_base_id=row.knowledge_base_id,
                knowledge_base_name=row.knowledge_base_name,
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                filename=row.filename,
                chunk_index=row.chunk_index,
                content=row.content,
                content_hash=row.content_hash,
                heading_path=row.heading_path,
                page_start=row.page_start,
                page_end=row.page_end,
                source_type=SourceType(row.source_type),
                source_locator=parse_source_locator(row.source_locator),
                token_count=row.token_count,
                # PostgreSQL 浮点误差可能产生极小越界，进入响应前收敛到合法范围。
                score=max(-1.0, min(1.0, float(row.score))),
            )
            for row in rows
        ]
