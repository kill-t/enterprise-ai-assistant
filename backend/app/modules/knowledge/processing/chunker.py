import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from app.modules.knowledge.processing.schemas import (
    ChunkDraft,
    ParsedDocument,
    ParsedSection,
)
from app.modules.knowledge.source_locator import build_legacy_source_locator


PARAGRAPH_SEPARATOR_PATTERN = re.compile(r"\n\s*\n")


class TextTokenCounter(Protocol):
    """描述 Chunker 需要的最小文本 Token 计数接口。"""

    def count(self, text: str) -> int:
        """返回文本的模型感知或保守估算 Token 数。"""
        ...


@dataclass(frozen=True, slots=True)
class _TextUnit:
    """保存可组合文本单元及其与前一单元之间的原始分隔符。"""

    content: str
    separator_before: str


class TokenAwareChunker:
    """在页码和标题边界内按 Token 预算进行段落感知分块。"""

    chunker_version = "token-paragraph-v1"

    def __init__(
        self,
        token_counter: TextTokenCounter,
        chunk_size_tokens: int = 600,
        chunk_overlap_tokens: int = 80,
    ) -> None:
        """校验分块预算，并保存可替换的 Token 计数器。"""
        if chunk_size_tokens <= 0:
            raise ValueError("Chunk Token 上限必须大于零")
        if chunk_overlap_tokens < 0:
            raise ValueError("Chunk 重叠 Token 不能小于零")
        if chunk_overlap_tokens >= chunk_size_tokens:
            raise ValueError("Chunk 重叠 Token 必须小于 Chunk Token 上限")
        self.token_counter = token_counter
        self.chunk_size_tokens = chunk_size_tokens
        self.chunk_overlap_tokens = chunk_overlap_tokens

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        """按来源段依次分块，并生成连续序号、来源元数据和内容哈希。"""
        drafts: list[ChunkDraft] = []
        for section in document.sections:
            for content in self._chunk_section(section):
                token_count = self.token_counter.count(content)
                drafts.append(
                    ChunkDraft(
                        chunk_index=len(drafts),
                        content=content,
                        content_hash=hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                        heading_path=(
                            " > ".join(section.heading_path)
                            if section.heading_path
                            else None
                        ),
                        page_start=section.page_number,
                        page_end=section.page_number,
                        token_count=token_count,
                        chunker_version=self.chunker_version,
                        source_locator=build_legacy_source_locator(
                            page_start=section.page_number,
                            page_end=section.page_number,
                            heading_path=section.heading_path,
                        ),
                    )
                )
        return drafts

    def _chunk_section(self, section: ParsedSection) -> list[str]:
        """在单一页码或标题路径内组合段落，绝不跨来源段传播重叠。"""
        units = self._build_units(section.content)
        if not units:
            return []

        chunks: list[str] = []
        current = ""
        for unit in units:
            if not current:
                current = unit.content
                continue

            candidate = current + unit.separator_before + unit.content
            if self.token_counter.count(candidate) <= self.chunk_size_tokens:
                current = candidate
                continue

            chunks.append(current)
            overlap = self._fit_overlap(
                previous=current,
                next_content=unit.content,
                separator=unit.separator_before,
            )
            if overlap:
                current = overlap + unit.separator_before + unit.content
            else:
                current = unit.content

        if current:
            chunks.append(current)
        return chunks

    def _build_units(self, content: str) -> list[_TextUnit]:
        """优先按空行拆段，仅对单个超预算段落执行强制切分。"""
        paragraphs = [
            paragraph.strip()
            for paragraph in PARAGRAPH_SEPARATOR_PATTERN.split(content.strip())
            if paragraph.strip()
        ]
        units: list[_TextUnit] = []
        for paragraph_index, paragraph in enumerate(paragraphs):
            pieces = self._split_oversized_text(paragraph)
            for piece_index, piece in enumerate(pieces):
                separator = ""
                if paragraph_index > 0 and piece_index == 0:
                    separator = "\n\n"
                units.append(
                    _TextUnit(
                        content=piece,
                        separator_before=separator,
                    )
                )
        return units

    def _split_oversized_text(self, text: str) -> list[str]:
        """通过二分查找切开超预算单段，避免依赖具体 tokenizer 的 decode。"""
        pieces: list[str] = []
        remaining = text
        while remaining:
            # 第一片可用完整预算；后续片为上一片的重叠预留空间。
            piece_budget = (
                self.chunk_size_tokens
                if not pieces
                else self.chunk_size_tokens - self.chunk_overlap_tokens
            )
            if self.token_counter.count(remaining) <= piece_budget:
                pieces.append(remaining)
                break

            prefix_length = self._max_prefix_length(
                remaining,
                piece_budget,
            )
            if prefix_length == 0:
                raise ValueError("单个字符已经超过 Chunk Token 上限")
            pieces.append(remaining[:prefix_length])
            remaining = remaining[prefix_length:]
        return pieces

    def _max_prefix_length(self, text: str, token_budget: int) -> int:
        """返回 Token 数不超过预算的最长字符前缀长度。"""
        low = 1
        high = len(text)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if self.token_counter.count(text[:middle]) <= token_budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _fit_overlap(
        self,
        previous: str,
        next_content: str,
        separator: str,
    ) -> str:
        """选择前块尽可能长的受限后缀，同时保证新块整体不超预算。"""
        if self.chunk_overlap_tokens == 0:
            return ""
        bounded_suffix = self._suffix_within_budget(
            previous,
            self.chunk_overlap_tokens,
        )
        low = 0
        high = len(bounded_suffix)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            suffix = bounded_suffix[middle:]
            candidate = suffix + separator + next_content if suffix else next_content
            if self.token_counter.count(candidate) <= self.chunk_size_tokens:
                best = suffix
                high = middle - 1
            else:
                low = middle + 1
        return best

    def _suffix_within_budget(self, text: str, token_budget: int) -> str:
        """返回 Token 数不超过预算的最长字符后缀。"""
        low = 0
        high = len(text)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            suffix = text[middle:]
            if self.token_counter.count(suffix) <= token_budget:
                best = suffix
                high = middle - 1
            else:
                low = middle + 1
        return best
