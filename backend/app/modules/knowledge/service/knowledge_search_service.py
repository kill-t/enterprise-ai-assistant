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
from app.modules.knowledge.search_models import MultiKnowledgeSearchCandidate
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    MultiKnowledgeSearchHit,
    MultiKnowledgeSearchResponse,
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
        # 显式值只用于测试或评测实验；None 时按知识库 Provider 动态选择阈值。
        self.similarity_threshold = similarity_threshold
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
        similarity_threshold = self._resolve_similarity_threshold(profile.provider)
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
            if candidate.score >= similarity_threshold
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
            similarity_threshold=similarity_threshold,
            results=[
                KnowledgeSearchHit.model_validate(candidate)
                for candidate in reliable_candidates
            ],
        )

    async def search_many(
        self,
        user_id: UUID,
        knowledge_bases: list[KnowledgeBaseEntity],
        request: KnowledgeSearchRequest,
    ) -> MultiKnowledgeSearchResponse:
        """使用一个可比较的查询向量检索多个同配置知识库。"""
        unique_knowledge_bases = self._normalize_multi_search_bases(
            user_id=user_id,
            knowledge_bases=knowledge_bases,
        )
        if not unique_knowledge_bases:
            return MultiKnowledgeSearchResponse(
                query=request.query,
                searched_knowledge_base_ids=[],
                similarity_threshold=self._resolve_similarity_threshold("mock"),
                results=[],
            )

        profile = self._get_shared_embedding_profile(unique_knowledge_bases)
        similarity_threshold = self._resolve_similarity_threshold(profile.provider)
        knowledge_base_ids = [item.id for item in unique_knowledge_bases]
        incompatible_ids = (
            await self.search_repository.list_incompatible_ready_knowledge_base_ids(
                user_id=user_id,
                knowledge_base_ids=knowledge_base_ids,
            )
        )
        if incompatible_ids:
            app_logger.warning(
                "会话知识库检索发现不兼容向量 user_id=%s knowledge_base_ids=%s",
                user_id,
                incompatible_ids,
            )
            raise self._configuration_mismatch_exception()

        embedding_result = await self._embed_query(request.query, profile=profile)
        self._ensure_query_vector_compatible(
            result=embedding_result,
            profile=profile,
        )
        candidates = (
            await self.search_repository.search_candidates_across_knowledge_bases(
                user_id=user_id,
                knowledge_base_ids=knowledge_base_ids,
                query_vector=embedding_result.vectors[0],
                embedding_model=profile.model,
                candidate_limit=request.top_k * self.candidate_multiplier,
            )
        )
        reliable_candidates = self._rank_unique_candidates(
            candidates=candidates,
            top_k=request.top_k,
            similarity_threshold=similarity_threshold,
        )

        app_logger.info(
            "多知识库向量检索完成 user_id=%s knowledge_base_count=%s candidates=%s reliable=%s top_k=%s",
            user_id,
            len(knowledge_base_ids),
            len(candidates),
            len(reliable_candidates),
            request.top_k,
        )
        return MultiKnowledgeSearchResponse(
            query=request.query,
            searched_knowledge_base_ids=knowledge_base_ids,
            similarity_threshold=similarity_threshold,
            results=[
                MultiKnowledgeSearchHit.model_validate(candidate)
                for candidate in reliable_candidates
            ],
        )

    @staticmethod
    def _normalize_multi_search_bases(
        user_id: UUID,
        knowledge_bases: list[KnowledgeBaseEntity],
    ) -> list[KnowledgeBaseEntity]:
        """校验内部调用边界并按首次出现顺序去掉重复知识库实体。"""
        if any(item.user_id != user_id for item in knowledge_bases):
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                status_code=404,
            )
        if any(item.is_archived for item in knowledge_bases):
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_ARCHIVED,
                message="知识库已归档，不能执行检索",
                status_code=409,
            )

        seen_ids: set[UUID] = set()
        unique_items: list[KnowledgeBaseEntity] = []
        for item in knowledge_bases:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                unique_items.append(item)
        return unique_items

    @staticmethod
    def _get_shared_embedding_profile(
        knowledge_bases: list[KnowledgeBaseEntity],
    ) -> EmbeddingProfile:
        """要求全部知识库使用同一向量空间，确保跨库分数可以比较。"""
        profiles = {
            EmbeddingProfile(
                provider=item.embedding_provider,
                model=item.embedding_model,
                dimension=item.embedding_dimension,
            )
            for item in knowledge_bases
        }
        if len(profiles) != 1:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_SEARCH_CONFIG_MISMATCH,
                message="所选知识库使用了不同的向量配置，请只选择相同配置的知识库",
                status_code=409,
            )
        return profiles.pop()

    def _rank_unique_candidates(
        self,
        candidates: list[MultiKnowledgeSearchCandidate],
        top_k: int,
        similarity_threshold: float,
    ) -> list[MultiKnowledgeSearchCandidate]:
        """按相似度全局排序，以正文哈希去重并返回达到阈值的 Top-K。"""
        ranked = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.score >= similarity_threshold
            ),
            key=lambda candidate: (
                -candidate.score,
                str(candidate.knowledge_base_id),
                str(candidate.document_id),
                candidate.chunk_index,
                str(candidate.chunk_id),
            ),
        )
        unique_candidates: list[MultiKnowledgeSearchCandidate] = []
        seen_content_hashes: set[str] = set()
        for candidate in ranked:
            if candidate.content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(candidate.content_hash)
            unique_candidates.append(candidate)
            if len(unique_candidates) == top_k:
                break
        return unique_candidates

    def _resolve_similarity_threshold(self, provider: str) -> float:
        """显式覆盖优先，否则按知识库实际 Provider 选择独立校准阈值。"""
        if self.similarity_threshold is not None:
            return self.similarity_threshold
        return settings.rag.similarity_threshold_for(provider)

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
