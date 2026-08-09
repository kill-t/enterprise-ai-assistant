import hashlib
from datetime import datetime
from io import BytesIO

from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.llm.token_counter import TokenCounter
from app.infrastructure.object_storage.base import BaseObjectStorage
from app.infrastructure.object_storage.errors import (
    ObjectStorageReadLimitExceededError,
)
from app.modules.knowledge.entity.document_ingestion_job import (
    DocumentIngestionJobEntity,
)
from app.modules.knowledge.processing.chunker import TokenAwareChunker
from app.modules.knowledge.processing.ingestion_schemas import (
    DocumentIngestionSource,
    ProcessedDocumentIngestion,
)
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.repository.document_index_repository import (
    DocumentIndexRepository,
)
from app.modules.knowledge.service.document_ingestion_failure_policy import (
    DocumentSourceIntegrityError,
    DocumentSourceNotFoundError,
    DocumentSourceUnavailableError,
)


class DocumentIngestionProcessor:
    """串联原文件下载、解析、分块、Embedding 和索引暂存。"""

    def __init__(
        self,
        document_repository: DocumentIndexRepository,
        object_storage: BaseObjectStorage,
        parser_registry: DocumentParserRegistry,
        embedding_factory: EmbeddingProviderFactory,
        chunk_size_tokens: int,
        chunk_overlap_tokens: int,
    ) -> None:
        """注入可替换基础设施，并保存第一版分块预算。"""
        self.document_repository = document_repository
        self.object_storage = object_storage
        self.parser_registry = parser_registry
        self.embedding_factory = embedding_factory
        self.chunk_size_tokens = chunk_size_tokens
        self.chunk_overlap_tokens = chunk_overlap_tokens

    async def prepare(
        self,
        job: DocumentIngestionJobEntity,
    ) -> DocumentIngestionSource:
        """复制文件定位和模型快照，使后续外部 I/O 不依赖 ORM 实体。"""
        source = await self.document_repository.get_processing_source(job.document_id)
        if source is None:
            raise DocumentSourceNotFoundError()
        return source

    async def process(
        self,
        source: DocumentIngestionSource,
    ) -> ProcessedDocumentIngestion:
        """在数据库事务外下载文件并生成与 Chunk 一一对应的向量。"""
        content = await self._download_and_verify(source)
        parser = self.parser_registry.get_parser(source.filename)
        parsed = await parser.parse(BytesIO(content))

        # Mock 没有厂商 tokenizer，显式使用保守估算；真实模型优先尝试模型 tokenizer。
        token_counter = TokenCounter(
            source.embedding_profile.model,
            encoder=False if source.embedding_profile.provider == "mock" else None,
        )
        chunker = TokenAwareChunker(
            token_counter=token_counter,
            chunk_size_tokens=self.chunk_size_tokens,
            chunk_overlap_tokens=self.chunk_overlap_tokens,
        )
        chunks = tuple(chunker.chunk(parsed))
        embedding_service = self.embedding_factory.create_service(
            source.embedding_profile
        )
        embedding_result = await embedding_service.embed_texts(
            [chunk.content for chunk in chunks]
        )
        return ProcessedDocumentIngestion(
            chunks=chunks,
            vectors=embedding_result.vectors,
            embedding_model=embedding_result.model,
            parser_version=parsed.parser_version,
            chunker_version=chunker.chunker_version,
            page_count=parsed.page_count,
        )

    async def persist(
        self,
        source: DocumentIngestionSource,
        processed: ProcessedDocumentIngestion,
        finished_at: datetime,
    ) -> None:
        """暂存 Chunk 替换和 READY 状态，交由 Worker 与任务成功一起提交。"""
        document_id = await self.document_repository.stage_success(
            source=source,
            processed=processed,
            finished_at=finished_at,
        )
        if document_id is None:
            raise DocumentSourceNotFoundError()

    async def _download_and_verify(self, source: DocumentIngestionSource) -> bytes:
        """限制下载大小并核对字节数、SHA-256，拒绝被替换的原文件。"""
        try:
            content = await self.object_storage.get_object(
                source.object_key,
                max_bytes=source.size_bytes,
            )
        except ObjectStorageReadLimitExceededError as exc:
            raise DocumentSourceIntegrityError() from exc
        except Exception as exc:
            raise DocumentSourceUnavailableError() from exc

        actual_sha256 = hashlib.sha256(content).hexdigest()
        if len(content) != source.size_bytes or actual_sha256 != source.sha256:
            raise DocumentSourceIntegrityError()
        return content
