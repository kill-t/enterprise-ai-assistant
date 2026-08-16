from dataclasses import dataclass, field

from app.modules.knowledge.source_locator import (
    DocumentSourceLocator,
    SourceLocator,
)


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """表示具有同一页码或 Markdown 标题路径的连续原文。"""

    content: str
    page_number: int | None = None
    heading_path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """表示 Parser 输出的有序来源段及文档级统计。"""

    sections: tuple[ParsedSection, ...]
    page_count: int
    parser_version: str


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """表示等待 Embedding、尚未写入数据库的可追溯 Chunk。"""

    chunk_index: int
    content: str
    content_hash: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    token_count: int
    chunker_version: str
    # 默认整篇位置只用于兼容手工构造的测试或 Adapter；正式 Chunker 会显式填写具体位置。
    source_locator: SourceLocator = field(default_factory=DocumentSourceLocator)
