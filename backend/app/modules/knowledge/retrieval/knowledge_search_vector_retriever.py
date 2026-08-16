"""把现有多知识库 SearchService 适配为混合检索的向量候选通道。"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.retrieval.evidence_selection import (
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.retrieval.vector_retriever import (
    BaseVectorRetriever,
    VectorRetrievalCandidate,
    VectorRetrievalError,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    VectorRetrievalScopeError,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
)
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


class KnowledgeSearchVectorRetriever(BaseVectorRetriever):
    """复用已验证的所有权、Embedding 和 pgvector 逻辑获取未筛选候选。"""

    def __init__(
        self,
        *,
        knowledge_base_repository: KnowledgeBaseRepository,
        search_service: KnowledgeSearchService,
    ) -> None:
        """注入知识库批量查询和配置为透传候选的现有检索服务。"""
        self.knowledge_base_repository = knowledge_base_repository
        self.search_service = search_service

    async def retrieve(
        self,
        request: VectorRetrievalRequest,
    ) -> VectorRetrievalResult:
        """恢复请求顺序、执行现有向量检索，并映射为渠道候选。"""
        try:
            knowledge_bases = (
                await self.knowledge_base_repository.list_by_ids_for_user(
                    user_id=request.user_id,
                    knowledge_base_ids=list(request.knowledge_base_ids),
                )
            )
            by_id = {knowledge_base.id: knowledge_base for knowledge_base in knowledge_bases}
            if set(by_id) != set(request.knowledge_base_ids):
                raise VectorRetrievalScopeError("知识库范围无效")

            # 数据库 IN 查询不保证顺序；恢复请求顺序便于日志、测试和响应保持稳定。
            ordered_knowledge_bases = [
                by_id[knowledge_base_id]
                for knowledge_base_id in request.knowledge_base_ids
            ]
            if any(item.is_archived for item in ordered_knowledge_bases):
                raise VectorRetrievalScopeError("知识库范围无效")

            response = await self.search_service.search_many(
                user_id=request.user_id,
                knowledge_bases=ordered_knowledge_bases,
                request=KnowledgeSearchRequest(
                    query=request.query,
                    top_k=request.channel_candidate_limit,
                ),
            )
            if tuple(response.searched_knowledge_base_ids) != request.knowledge_base_ids:
                raise VectorRetrievalError("向量检索返回了错误的知识库范围")

            result = VectorRetrievalResult(
                candidates=tuple(
                    VectorRetrievalCandidate(
                        knowledge_base_id=hit.knowledge_base_id,
                        knowledge_base_name=hit.knowledge_base_name,
                        chunk_id=hit.chunk_id,
                        document_id=hit.document_id,
                        filename=hit.filename,
                        chunk_index=hit.chunk_index,
                        content=hit.content,
                        heading_path=hit.heading_path,
                        page_start=hit.page_start,
                        page_end=hit.page_end,
                        source_type=hit.source_type,
                        source_locator=hit.source_locator,
                        token_count=hit.token_count,
                        vector_score=hit.score,
                    )
                    for hit in response.results
                )
            )
            result.ensure_valid_for(request)
            return result
        except VectorRetrievalScopeError:
            raise
        except BusinessException as exc:
            if exc.code in {
                ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                ErrorCode.KNOWLEDGE_BASE_ARCHIVED,
            }:
                raise VectorRetrievalScopeError("知识库范围无效") from exc
            app_logger.warning(
                "向量候选检索失败 code=%s knowledge_base_count=%s",
                exc.code.value,
                len(request.knowledge_base_ids),
            )
            raise VectorRetrievalError("向量检索服务暂时不可用") from exc
        except (SQLAlchemyError, ValueError) as exc:
            app_logger.warning(
                "向量候选检索失败 reason=%s knowledge_base_count=%s",
                type(exc).__name__,
                len(request.knowledge_base_ids),
            )
            raise VectorRetrievalError("向量检索服务暂时不可用") from exc


def create_knowledge_search_vector_retriever(
    *,
    db: AsyncSession,
    embedding_factory: EmbeddingProviderFactory | None = None,
) -> KnowledgeSearchVectorRetriever:
    """组装复用现有 Service 的真实向量通道，并关闭旧阈值的提前过滤。"""
    factory = embedding_factory or EmbeddingProviderFactory()
    # -1 接受全部合法余弦分数，candidate_multiplier=1 保证 Top-K 就是渠道预算。
    search_service = KnowledgeSearchService(
        db=db,
        embedding_factory=factory,
        similarity_threshold=-1.0,
        candidate_multiplier=1,
        evidence_selector=ThresholdEvidenceSelector(),
    )
    return KnowledgeSearchVectorRetriever(
        knowledge_base_repository=KnowledgeBaseRepository(db),
        search_service=search_service,
    )
