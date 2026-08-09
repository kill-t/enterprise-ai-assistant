"""定义知识检索 Repository 与 Service 之间传递的内部数据。"""

from dataclasses import dataclass
from uuid import UUID


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
