"""定义知识检索 Repository 与 Service 之间传递的内部数据。"""

from dataclasses import dataclass, field
from uuid import UUID

from app.common.enums import SourceType
from app.modules.knowledge.source_locator import (
    DocumentSourceLocator,
    SourceLocator,
)


@dataclass(frozen=True, slots=True)
class KnowledgeSearchCandidate:
    """表示数据库按向量距离召回的一条可追溯 Chunk 候选。"""

    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    score: float
    source_type: SourceType = SourceType.TEXT
    source_locator: SourceLocator = field(default_factory=DocumentSourceLocator)


@dataclass(frozen=True, slots=True)
class MultiKnowledgeSearchCandidate:
    """表示跨知识库召回的一条候选，并携带去重和来源所需的额外字段。"""

    knowledge_base_id: UUID
    knowledge_base_name: str
    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    content_hash: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    score: float
    source_type: SourceType = SourceType.TEXT
    source_locator: SourceLocator = field(default_factory=DocumentSourceLocator)
