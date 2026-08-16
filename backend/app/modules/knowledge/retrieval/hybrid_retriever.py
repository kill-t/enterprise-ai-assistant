"""编排向量和词法候选，并使用 RRF 生成可解释的混合候选榜。"""

import math
from dataclasses import dataclass, field
from uuid import UUID

from app.common.enums import SourceType

from app.core.loggers import app_logger
from app.modules.knowledge.retrieval.lexical_retriever import (
    BaseLexicalRetriever,
    LexicalRetrievalCandidate,
    LexicalRetrievalError,
    LexicalRetrievalRequest,
)
from app.modules.knowledge.retrieval.reciprocal_rank_fusion import (
    RankedRetrievalList,
    ReciprocalRankFusion,
    RetrievalChannel,
    RrfRankContribution,
)
from app.modules.knowledge.retrieval.vector_retriever import (
    BaseVectorRetriever,
    VectorRetrievalCandidate,
    VectorRetrievalError,
    VectorRetrievalScopeError,
)
from app.modules.knowledge.source_locator import (
    DocumentSourceLocator,
    SourceLocator,
)


class HybridRetrievalError(RuntimeError):
    """表示两路都不可用或候选来源互相矛盾。"""


@dataclass(frozen=True, slots=True)
class HybridRetrievalRequest:
    """描述一次混合检索的权限范围、渠道预算和最终结果预算。"""

    user_id: UUID
    knowledge_base_ids: tuple[UUID, ...]
    query: str
    channel_candidate_limit: int
    result_limit: int

    def __post_init__(self) -> None:
        """清理问题与重复知识库，并限制现有向量适配器可支持的 Top-K。"""
        normalized_query = self.query.strip()
        if not normalized_query:
            raise ValueError("混合检索 query 不能为空")
        if not self.knowledge_base_ids:
            raise ValueError("混合检索至少需要一个知识库")
        if not 1 <= self.channel_candidate_limit <= 20:
            raise ValueError(
                "混合检索 channel_candidate_limit 必须在 1 到 20 之间"
            )
        if not 1 <= self.result_limit <= 20:
            raise ValueError("混合检索 result_limit 必须在 1 到 20 之间")

        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(
            self,
            "knowledge_base_ids",
            tuple(dict.fromkeys(self.knowledge_base_ids)),
        )


@dataclass(frozen=True, slots=True)
class HybridRetrievalCandidate:
    """保存融合分数、两路原始分数、名次贡献和完整引用来源。"""

    knowledge_base_id: UUID
    knowledge_base_name: str
    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    fused_score: float
    vector_score: float | None
    lexical_score: float | None
    contributions: tuple[RrfRankContribution, ...]
    source_type: SourceType = SourceType.TEXT
    source_locator: SourceLocator = field(default_factory=DocumentSourceLocator)

    def __post_init__(self) -> None:
        """候选至少来自一个渠道，且融合分数必须可排序。"""
        if self.vector_score is None and self.lexical_score is None:
            raise ValueError("混合候选至少需要一个渠道分数")
        if not math.isfinite(self.fused_score) or self.fused_score <= 0:
            raise ValueError("RRF 融合分数必须是大于零的有限数")
        if not self.contributions:
            raise ValueError("混合候选必须保留至少一个渠道贡献")


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """返回有序融合候选，并公开成功和降级的渠道状态。"""

    candidates: tuple[HybridRetrievalCandidate, ...]
    successful_channels: tuple[RetrievalChannel, ...]
    degraded_channels: tuple[RetrievalChannel, ...]

    def __post_init__(self) -> None:
        """防止渠道状态冲突、重复 Chunk 或错误融合顺序进入下游。"""
        if not self.successful_channels:
            raise ValueError("混合检索至少需要一个成功渠道")
        if len(self.successful_channels) != len(set(self.successful_channels)):
            raise ValueError("混合检索成功渠道不能重复")
        if len(self.degraded_channels) != len(set(self.degraded_channels)):
            raise ValueError("混合检索降级渠道不能重复")
        if set(self.successful_channels) & set(self.degraded_channels):
            raise ValueError("同一渠道不能同时标记为成功和降级")

        chunk_ids = [candidate.chunk_id for candidate in self.candidates]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("混合检索结果不能包含重复 Chunk")
        scores = [candidate.fused_score for candidate in self.candidates]
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ValueError("混合检索结果必须按 fused_score 降序排列")

    def ensure_valid_for(self, request: HybridRetrievalRequest) -> None:
        """限制最终数量，并阻止融合过程生成请求范围外的来源。"""
        if len(self.candidates) > request.result_limit:
            raise HybridRetrievalError("混合检索结果超过最终候选预算")
        allowed_ids = set(request.knowledge_base_ids)
        if any(
            candidate.knowledge_base_id not in allowed_ids
            for candidate in self.candidates
        ):
            raise HybridRetrievalError("混合检索结果包含知识库范围之外的 Chunk")


class HybridRetriever:
    """依次调用两个独立渠道，执行显式降级并融合成功榜单。"""

    def __init__(
        self,
        *,
        vector_retriever: BaseVectorRetriever,
        lexical_retriever: BaseLexicalRetriever,
        fusion: ReciprocalRankFusion | None = None,
    ) -> None:
        """注入两路候选实现和纯 RRF 算法，便于独立替换与测试。"""
        self.vector_retriever = vector_retriever
        self.lexical_retriever = lexical_retriever
        self.fusion = fusion or ReciprocalRankFusion()

    async def retrieve(
        self,
        request: HybridRetrievalRequest,
    ) -> HybridRetrievalResult:
        """收集两路候选；一路失败时降级，两路失败时返回系统错误。"""
        successful_channels: list[RetrievalChannel] = []
        degraded_channels: list[RetrievalChannel] = []

        # 默认真实 Adapter 可以共享一个 AsyncSession；它不支持并发使用，所以两路按顺序执行。
        try:
            vector_result = await self.vector_retriever.retrieve(request)
            vector_result.ensure_valid_for(request)
            vector_candidates = vector_result.candidates
            successful_channels.append(RetrievalChannel.VECTOR)
        except VectorRetrievalScopeError:
            # 权限与资源状态不是可用性故障，必须保留给上层转换为 404/409。
            raise
        except VectorRetrievalError:
            vector_candidates = ()
            degraded_channels.append(RetrievalChannel.VECTOR)
            app_logger.warning(
                "混合检索向量渠道降级 knowledge_base_count=%s",
                len(request.knowledge_base_ids),
            )

        lexical_request = LexicalRetrievalRequest(
            user_id=request.user_id,
            knowledge_base_ids=request.knowledge_base_ids,
            query=request.query,
            candidate_limit=request.channel_candidate_limit,
        )
        try:
            lexical_result = await self.lexical_retriever.retrieve(lexical_request)
            lexical_result.ensure_valid_for(lexical_request)
            lexical_candidates = lexical_result.candidates
            successful_channels.append(RetrievalChannel.LEXICAL)
        except LexicalRetrievalError:
            lexical_candidates = ()
            degraded_channels.append(RetrievalChannel.LEXICAL)
            app_logger.warning(
                "混合检索词法渠道降级 knowledge_base_count=%s",
                len(request.knowledge_base_ids),
            )

        if not successful_channels:
            app_logger.error(
                "混合检索全部渠道不可用 knowledge_base_count=%s",
                len(request.knowledge_base_ids),
            )
            raise HybridRetrievalError("混合检索服务暂时不可用")

        candidates = self._fuse_candidates(
            vector_candidates=vector_candidates,
            lexical_candidates=lexical_candidates,
            result_limit=request.result_limit,
        )
        result = HybridRetrievalResult(
            candidates=candidates,
            successful_channels=tuple(successful_channels),
            degraded_channels=tuple(degraded_channels),
        )
        result.ensure_valid_for(request)
        app_logger.info(
            "混合检索候选完成 vector=%s lexical=%s fused=%s degraded=%s",
            len(vector_candidates),
            len(lexical_candidates),
            len(candidates),
            [channel.value for channel in degraded_channels],
        )
        return result

    def _fuse_candidates(
        self,
        *,
        vector_candidates: tuple[VectorRetrievalCandidate, ...],
        lexical_candidates: tuple[LexicalRetrievalCandidate, ...],
        result_limit: int,
    ) -> tuple[HybridRetrievalCandidate, ...]:
        """根据 Chunk ID 融合榜单，并核对共同候选的来源字段完全一致。"""
        vector_by_id = {candidate.chunk_id: candidate for candidate in vector_candidates}
        lexical_by_id = {
            candidate.chunk_id: candidate for candidate in lexical_candidates
        }
        for chunk_id in vector_by_id.keys() & lexical_by_id.keys():
            if not self._same_provenance(vector_by_id[chunk_id], lexical_by_id[chunk_id]):
                raise HybridRetrievalError("混合检索渠道返回的来源信息不一致")

        rankings: list[RankedRetrievalList] = []
        if vector_candidates:
            rankings.append(
                RankedRetrievalList(
                    channel=RetrievalChannel.VECTOR,
                    chunk_ids=tuple(item.chunk_id for item in vector_candidates),
                )
            )
        if lexical_candidates:
            rankings.append(
                RankedRetrievalList(
                    channel=RetrievalChannel.LEXICAL,
                    chunk_ids=tuple(item.chunk_id for item in lexical_candidates),
                )
            )
        fused = self.fusion.fuse(tuple(rankings), limit=result_limit)

        results: list[HybridRetrievalCandidate] = []
        for fused_item in fused:
            vector = vector_by_id.get(fused_item.chunk_id)
            lexical = lexical_by_id.get(fused_item.chunk_id)
            source = vector or lexical
            if source is None:  # pragma: no cover - RRF 只接收上面两个候选映射的 ID。
                raise HybridRetrievalError("RRF 返回了未知 Chunk")
            results.append(
                HybridRetrievalCandidate(
                    knowledge_base_id=source.knowledge_base_id,
                    knowledge_base_name=source.knowledge_base_name,
                    chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    filename=source.filename,
                    chunk_index=source.chunk_index,
                    content=source.content,
                    heading_path=source.heading_path,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    source_type=source.source_type,
                    source_locator=source.source_locator,
                    token_count=source.token_count,
                    fused_score=fused_item.fused_score,
                    vector_score=(vector.vector_score if vector is not None else None),
                    lexical_score=(
                        lexical.lexical_score if lexical is not None else None
                    ),
                    contributions=fused_item.contributions,
                )
            )
        return tuple(results)

    @staticmethod
    def _same_provenance(
        vector: VectorRetrievalCandidate,
        lexical: LexicalRetrievalCandidate,
    ) -> bool:
        """比较引用需要的字段，防止同一 Chunk ID 在两路中指向不同内容。"""
        # 知识库名称可以并发重命名，不属于 Chunk 身份；其余字段应在两路读取中一致。
        return (
            vector.knowledge_base_id,
            vector.document_id,
            vector.filename,
            vector.chunk_index,
            vector.content,
            vector.heading_path,
            vector.page_start,
            vector.page_end,
            vector.source_type,
            vector.source_locator,
            vector.token_count,
        ) == (
            lexical.knowledge_base_id,
            lexical.document_id,
            lexical.filename,
            lexical.chunk_index,
            lexical.content,
            lexical.heading_path,
            lexical.page_start,
            lexical.page_end,
            lexical.source_type,
            lexical.source_locator,
            lexical.token_count,
        )
