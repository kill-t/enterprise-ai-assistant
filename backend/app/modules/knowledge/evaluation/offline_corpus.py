"""在不启动 PostgreSQL/MinIO 的情况下运行多格式 Parser 与 Mock 向量回归。"""

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.common.enums import SourceType
from app.core.config import EmbeddingSettings
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.llm.token_counter import TokenCounter
from app.modules.knowledge.evaluation.models import RagEvaluationReport
from app.modules.knowledge.processing.chunker import TokenAwareChunker
from app.modules.knowledge.processing.errors import UnsupportedDocumentTypeError
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.processing.schemas import ChunkDraft
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.modules.knowledge.source_locator import infer_source_type

OFFLINE_KNOWLEDGE_BASE_ID = uuid5(
    NAMESPACE_URL,
    "enterprise-ai-assistant:offline-multiformat-evaluation",
)


class OfflineCorpusError(ValueError):
    """表示本地语料目录或离线索引状态不适合形成可信评测。"""


@dataclass(frozen=True, slots=True)
class _OfflineIndexedChunk:
    """保存离线评测需要的 Chunk 来源与已归一化 Mock 向量。"""

    chunk_id: UUID
    document_id: UUID
    filename: str
    source_type: SourceType
    chunk: ChunkDraft
    vector: tuple[float, ...]


class OfflineCorpusSearchService:
    """复用正式 Parser/Chunker/Embedding，并以进程内余弦排序替代 pgvector。"""

    knowledge_base_id = OFFLINE_KNOWLEDGE_BASE_ID

    def __init__(
        self,
        *,
        chunks: tuple[_OfflineIndexedChunk, ...],
        embedding_factory: EmbeddingProviderFactory,
        similarity_threshold: float,
    ) -> None:
        """保存不可变索引与查询 Embedding 配置。"""
        if not -1.0 <= similarity_threshold <= 1.0:
            raise OfflineCorpusError("离线评测相似度阈值必须位于 -1 到 1")
        self.chunks = chunks
        self.embedding_factory = embedding_factory
        self.similarity_threshold = similarity_threshold
        self.profile = embedding_factory.default_profile()

    @classmethod
    async def build(
        cls,
        *,
        corpus_directory: str | Path,
        similarity_threshold: float = 0.2,
        parser_registry: DocumentParserRegistry | None = None,
        embedding_settings: EmbeddingSettings | None = None,
        chunk_size_tokens: int = 600,
        chunk_overlap_tokens: int = 80,
    ) -> "OfflineCorpusSearchService":
        """按文件名稳定顺序解析全部语料并一次性建立本地 Mock 索引。"""
        directory = Path(corpus_directory)
        if not directory.is_dir():
            raise OfflineCorpusError("离线评测语料目录不存在")
        paths = tuple(sorted(path for path in directory.iterdir() if path.is_file()))
        if not paths:
            raise OfflineCorpusError("离线评测语料至少包含一份文档")

        registry = parser_registry or DocumentParserRegistry()
        resolved_settings = embedding_settings or EmbeddingSettings(provider="mock")
        if resolved_settings.provider != "mock":
            raise OfflineCorpusError("离线多格式基线只允许使用无额度 Mock Embedding")
        embedding_factory = EmbeddingProviderFactory(resolved_settings)
        profile = embedding_factory.default_profile()
        token_counter = TokenCounter(profile.model, encoder=False)
        chunker = TokenAwareChunker(
            token_counter=token_counter,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )

        pending_chunks: list[tuple[Path, SourceType, UUID, ChunkDraft]] = []
        for path in paths:
            try:
                parser = registry.get_parser(path.name)
                source_type = infer_source_type(path.name)
            except (UnsupportedDocumentTypeError, ValueError) as exc:
                raise OfflineCorpusError(f"发现不支持的评测语料：{path.name}") from exc
            content = await asyncio.to_thread(path.read_bytes)
            parsed = await parser.parse(BytesIO(content))
            document_id = uuid5(
                NAMESPACE_URL,
                f"enterprise-ai-assistant:offline-document:{path.name}",
            )
            for chunk in chunker.chunk(parsed):
                pending_chunks.append((path, source_type, document_id, chunk))

        if not pending_chunks:
            raise OfflineCorpusError("离线评测语料没有产生任何 Chunk")
        embedding_service = embedding_factory.create_service(profile)
        embedded = await embedding_service.embed_texts(
            [chunk.content for _, _, _, chunk in pending_chunks]
        )
        chunks = tuple(
            _OfflineIndexedChunk(
                chunk_id=uuid5(
                    NAMESPACE_URL,
                    (
                        "enterprise-ai-assistant:offline-chunk:"
                        f"{path.name}:{chunk.chunk_index}:{chunk.content_hash}"
                    ),
                ),
                document_id=document_id,
                filename=path.name,
                source_type=source_type,
                chunk=chunk,
                vector=vector,
            )
            for (path, source_type, document_id, chunk), vector in zip(
                pending_chunks,
                embedded.vectors,
                strict=True,
            )
        )
        return cls(
            chunks=chunks,
            embedding_factory=embedding_factory,
            similarity_threshold=similarity_threshold,
        )

    async def search(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        request: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResponse:
        """生成查询向量并按与 pgvector 相同的余弦相似度口径排序。"""
        del user_id  # 离线格式回归不承担在线用户权限评测。
        if knowledge_base_id != self.knowledge_base_id:
            raise OfflineCorpusError("离线评测请求使用了错误知识库标识")
        embedding_service = self.embedding_factory.create_service(self.profile)
        query_result = await embedding_service.embed_texts([request.query])
        query_vector = query_result.vectors[0]
        ranked = sorted(
            (
                (self._cosine_similarity(query_vector, chunk.vector), chunk)
                for chunk in self.chunks
            ),
            key=lambda item: (-item[0], str(item[1].chunk_id)),
        )
        hits = [
            self._to_search_hit(chunk, score)
            for score, chunk in ranked
            if score >= self.similarity_threshold
        ][: request.top_k]
        return KnowledgeSearchResponse(
            knowledge_base_id=self.knowledge_base_id,
            query=request.query,
            embedding_model=query_result.model,
            similarity_threshold=self.similarity_threshold,
            results=hits,
        )

    @staticmethod
    def _cosine_similarity(
        left: tuple[float, ...],
        right: tuple[float, ...],
    ) -> float:
        """Mock 向量已 L2 归一化，点积即余弦相似度。"""
        score = sum(
            left_value * right_value
            for left_value, right_value in zip(left, right, strict=True)
        )
        # 浮点累加可能略微越过边界，响应契约要求限制在 -1 到 1。
        return max(-1.0, min(1.0, score))

    @staticmethod
    def _to_search_hit(
        indexed: _OfflineIndexedChunk,
        score: float,
    ) -> KnowledgeSearchHit:
        """把离线索引条目还原成正式 Search Service 的响应契约。"""
        chunk = indexed.chunk
        return KnowledgeSearchHit(
            chunk_id=indexed.chunk_id,
            document_id=indexed.document_id,
            filename=indexed.filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            heading_path=chunk.heading_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            token_count=chunk.token_count,
            score=score,
            source_type=indexed.source_type,
            source_locator=chunk.source_locator,
        )


def sanitize_report_for_baseline(
    report: RagEvaluationReport,
) -> RagEvaluationReport:
    """删除来源 ID 和机器相关延迟，只保留可重复比较的质量基线。"""
    return report.model_copy(
        update={
            "average_latency_ms": None,
            "p95_latency_ms": None,
            "results": tuple(
                result.model_copy(
                    update={
                        "retrieved_sources": (),
                        "latency_ms": None,
                    }
                )
                for result in report.results
            ),
        }
    )
