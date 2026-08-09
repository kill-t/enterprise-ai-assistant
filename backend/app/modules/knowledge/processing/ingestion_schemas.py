from dataclasses import dataclass
from uuid import UUID

from app.infrastructure.embedding.schemas import EmbeddingProfile
from app.modules.knowledge.processing.schemas import ChunkDraft


@dataclass(frozen=True, slots=True)
class DocumentIngestionSource:
    """保存 Worker 在释放数据库事务后仍需要的原文件与模型快照。"""

    document_id: UUID
    filename: str
    object_key: str
    size_bytes: int
    sha256: str
    embedding_profile: EmbeddingProfile


@dataclass(frozen=True, slots=True)
class ProcessedDocumentIngestion:
    """保存等待原子写库的 Chunk、向量和处理版本信息。"""

    chunks: tuple[ChunkDraft, ...]
    vectors: tuple[tuple[float, ...], ...]
    embedding_model: str
    parser_version: str
    chunker_version: str
    page_count: int
