"""封装带所有权过滤的 PostgreSQL pg_trgm 关键词候选查询。"""

from uuid import UUID

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import DocumentStatus, SourceType
# Repository 从聚合入口导入实体，确保字符串 relationship 依赖都已注册。
from app.modules.knowledge.models import (
    DocumentChunkEntity,
    DocumentEntity,
    KnowledgeBaseEntity,
)
from app.modules.knowledge.retrieval.lexical_retriever import (
    LexicalRetrievalCandidate,
)
from app.modules.knowledge.source_locator import parse_source_locator


class PgTrgmLexicalRepository:
    """只负责构造和执行 pg_trgm SQL，不决定混合检索降级策略。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存当前请求生命周期内共用的异步数据库 Session。"""
        self.db = db

    async def search_candidates(
        self,
        *,
        user_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        query: str,
        candidate_limit: int,
        minimum_similarity: float,
    ) -> list[LexicalRetrievalCandidate]:
        """在 SQL 层完成权限、文档状态、相似度过滤和全局 Top-K。"""
        if not knowledge_base_ids:
            return []

        # `true` 将阈值限制在当前事务，避免连接回到池后影响下一次请求。
        await self.db.execute(
            text(
                "SELECT set_config("
                "'pg_trgm.word_similarity_threshold', :threshold, true)"
            ),
            {"threshold": str(minimum_similarity)},
        )

        # 一个命名绑定参数同时用于分数和距离，避免数据库收到两份查询正文。
        query_parameter = bindparam("lexical_query", value=query)
        lexical_score = func.word_similarity(
            query_parameter,
            DocumentChunkEntity.content,
        ).label("lexical_score")
        # `query <<-> content` 是 word_similarity 对应的 GiST KNN 距离运算。
        lexical_distance = query_parameter.op("<<->")(
            DocumentChunkEntity.content
        )
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
                lexical_score,
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
                DocumentEntity.status == DocumentStatus.READY,
                # 可索引操作符使用上面设置的事务级阈值；普通函数比较不会走 trigram 索引。
                query_parameter.bool_op("<%")(
                    DocumentChunkEntity.content
                ),
            )
            # 直接按 pg_trgm 距离运算符排序并 LIMIT，允许 GiST 执行 KNN 扫描。
            .order_by(lexical_distance.asc())
            .limit(candidate_limit)
        )
        rows = (await self.db.execute(statement)).all()
        return [
            LexicalRetrievalCandidate(
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
                lexical_score=max(0.0, min(1.0, float(row.lexical_score))),
            )
            for row in rows
        ]
