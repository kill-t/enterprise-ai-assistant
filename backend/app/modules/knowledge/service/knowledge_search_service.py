"""编排知识库所有权、查询向量生成与可靠性过滤。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.config import settings
from app.core.loggers import app_logger
from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.embedding.schemas import EmbeddingProfile, EmbeddingResult
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.repository.knowledge_search_repository import (
    KnowledgeSearchRepository,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)


class KnowledgeSearchService:
    """把用户问题转换为安全、可追溯且达到阈值的 Chunk 结果。"""

    def __init__(
        self,
        db: AsyncSession,
        embedding_factory: EmbeddingProviderFactory | None = None,
        similarity_threshold: float | None = None,
        candidate_multiplier: int | None = None,
    ) -> None:
        """组装只读 Repository、Embedding 工厂和后端检索规则。"""
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.search_repository = KnowledgeSearchRepository(db)
        self.embedding_factory = embedding_factory or EmbeddingProviderFactory()
        self.similarity_threshold = (
            settings.rag.search_similarity_threshold
            if similarity_threshold is None
            else similarity_threshold
        )
        self.candidate_multiplier = (
            settings.rag.search_candidate_multiplier
            if candidate_multiplier is None
            else candidate_multiplier
        )

    async def search(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        request: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResponse:
        """验证知识库后生成查询向量，并返回达到可靠性阈值的 Top-K 来源。"""
        knowledge_base = await self._get_searchable_knowledge_base(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
        await self._ensure_stored_vectors_compatible(
            user_id=user_id,
            knowledge_base=knowledge_base,
        )
        profile = EmbeddingProfile(
            provider=knowledge_base.embedding_provider,
            model=knowledge_base.embedding_model,
            dimension=knowledge_base.embedding_dimension,
        )
        embedding_result = await self._embed_query(
            request.query,
            profile=profile,
        )
        self._ensure_query_vector_compatible(
            result=embedding_result,
            profile=profile,
        )

        candidates = await self.search_repository.search_candidates(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            query_vector=embedding_result.vectors[0],
            embedding_model=profile.model,
            candidate_limit=request.top_k * self.candidate_multiplier,
        )
        reliable_candidates = [
            candidate
            for candidate in candidates
            if candidate.score >= self.similarity_threshold
        ][: request.top_k]

        app_logger.info(
            "知识库向量检索完成 user_id=%s knowledge_base_id=%s candidates=%s reliable=%s top_k=%s",
            user_id,
            knowledge_base_id,
            len(candidates),
            len(reliable_candidates),
            request.top_k,
        )
        return KnowledgeSearchResponse(
            knowledge_base_id=knowledge_base_id,
            query=request.query,
            embedding_model=profile.model,
            similarity_threshold=self.similarity_threshold,
            results=[
                KnowledgeSearchHit.model_validate(candidate)
                for candidate in reliable_candidates
            ],
        )

    async def _get_searchable_knowledge_base(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> KnowledgeBaseEntity:
        """先执行所有权查询，越权与不存在统一返回 404 且不调用模型。"""
        knowledge_base = await self.knowledge_base_repository.get_by_id_for_user(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        if knowledge_base is None:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                status_code=404,
            )
        if knowledge_base.is_archived:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_ARCHIVED,
                message="知识库已归档，不能执行检索",
                status_code=409,
            )
        return knowledge_base

    async def _ensure_stored_vectors_compatible(
        self,
        user_id: UUID,
        knowledge_base: KnowledgeBaseEntity,
    ) -> None:
        """在产生模型费用前阻止混用不同 Embedding 模型的旧索引。"""
        incompatible = (
            await self.search_repository.has_incompatible_ready_chunks(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                embedding_model=knowledge_base.embedding_model,
            )
        )
        if incompatible:
            raise self._configuration_mismatch_exception()

    async def _embed_query(
        self,
        query: str,
        profile: EmbeddingProfile,
    ) -> EmbeddingResult:
        """使用知识库冻结的配置生成查询向量，并隐藏第三方异常细节。"""
        try:
            service = self.embedding_factory.create_service(profile)
            return await service.embed_texts([query])
        except EmbeddingError as exc:
            app_logger.warning(
                "知识库查询向量生成失败 provider=%s model=%s code=%s",
                profile.provider,
                profile.model,
                exc.code.value,
            )
            if exc.code in {
                EmbeddingErrorCode.CONFIGURATION_INVALID,
                EmbeddingErrorCode.DIMENSION_MISMATCH,
            }:
                raise self._configuration_mismatch_exception() from exc
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE,
                message="向量服务暂时不可用，请稍后重试",
                status_code=503,
            ) from exc

    @staticmethod
    def _ensure_query_vector_compatible(
        result: EmbeddingResult,
        profile: EmbeddingProfile,
    ) -> None:
        """对注入实现增加最后一道模型、维度和单向量数量保护。"""
        if (
            result.model != profile.model
            or result.dimension != profile.dimension
            or len(result.vectors) != 1
            or len(result.vectors[0]) != profile.dimension
        ):
            raise KnowledgeSearchService._configuration_mismatch_exception()

    @staticmethod
    def _configuration_mismatch_exception() -> BusinessException:
        """构造提示重新入库的稳定配置冲突错误。"""
        return BusinessException(
            code=ErrorCode.KNOWLEDGE_SEARCH_CONFIG_MISMATCH,
            message="知识库向量配置不一致，请重新入库文档",
            status_code=409,
        )
