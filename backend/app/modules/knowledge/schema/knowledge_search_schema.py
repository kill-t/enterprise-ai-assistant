"""知识库向量检索的 HTTP 请求与响应契约。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeSearchRequest(BaseModel):
    """限制用户问题长度和单次返回数量，避免无边界查询。"""

    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        """在长度校验前清理问题两端空格，使纯空白问题被正确拒绝。"""
        if isinstance(value, str):
            return value.strip()
        return value


class KnowledgeSearchHit(BaseModel):
    """返回一条带原文和来源位置的可靠检索结果。"""

    chunk_id: UUID
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    score: float = Field(ge=-1.0, le=1.0)

    model_config = ConfigDict(from_attributes=True)


class KnowledgeSearchResponse(BaseModel):
    """返回本次检索配置和按相关性排序的来源列表。"""

    knowledge_base_id: UUID
    query: str
    embedding_model: str
    similarity_threshold: float
    results: list[KnowledgeSearchHit]


class MultiKnowledgeSearchHit(KnowledgeSearchHit):
    """在普通 Chunk 来源上补充所属知识库，供聊天引用和审计使用。"""

    knowledge_base_id: UUID
    knowledge_base_name: str


class MultiKnowledgeSearchResponse(BaseModel):
    """返回多个同配置知识库经过全局排序后的统一证据列表。"""

    query: str
    searched_knowledge_base_ids: list[UUID]
    similarity_threshold: float
    results: list[MultiKnowledgeSearchHit]
