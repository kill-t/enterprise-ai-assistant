"""把 Word OOXML 文档解析成保持正文顺序的结构化来源段。"""

import re
from collections import defaultdict
from io import BytesIO
from typing import BinaryIO, Literal
from zipfile import BadZipFile, LargeZipFile, ZipFile

from docx import Document
from docx.document import Document as DocxDocument
from docx.opc.exceptions import OpcError
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.source_locator import SectionSourceLocator


DEFAULT_MAX_ARCHIVE_ENTRIES = 2_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_ENTRY_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 500
REQUIRED_PACKAGE_PARTS = {"[Content_Types].xml", "word/document.xml"}
MACRO_PART_SUFFIX = "vbaproject.bin"
HEADING_STYLE_PATTERN = re.compile(r"^(?:heading|标题)\s*([1-6])$", re.IGNORECASE)
HEADING_STYLE_ID_PATTERN = re.compile(r"^heading([1-6])$", re.IGNORECASE)
LIST_STYLE_MARKERS = ("list", "bullet", "number", "列表", "项目符号", "编号")


class DocxParser(BaseDocumentParser):
    """读取 `.docx` 正文、列表和表格，并拒绝危险的 OOXML 压缩包。"""

    parser_version = "docx-python-docx-v1"

    def __init__(
        self,
        *,
        max_archive_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES,
        max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
        max_entry_uncompressed_bytes: int = DEFAULT_MAX_ENTRY_UNCOMPRESSED_BYTES,
        max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
    ) -> None:
        """保存压缩包资源上限，测试可使用更小上限验证安全失败路径。"""
        limits = (
            max_archive_entries,
            max_uncompressed_bytes,
            max_entry_uncompressed_bytes,
            max_compression_ratio,
        )
        if any(limit <= 0 for limit in limits):
            raise ValueError("DOCX 压缩包资源上限必须大于零")
        self.max_archive_entries = max_archive_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_entry_uncompressed_bytes = max_entry_uncompressed_bytes
        self.max_compression_ratio = max_compression_ratio

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """先检查 OOXML 容器，再按 Word 正文中的真实段落/表格顺序提取内容。"""
        stream.seek(0)
        content = stream.read()
        self._validate_archive(content)

        try:
            document = Document(BytesIO(content))
            sections = self._parse_body(document)
        except DocumentParseError:
            raise
        except (
            BadZipFile,
            LargeZipFile,
            OpcError,
            XMLSyntaxError,
            KeyError,
            ValueError,
        ) as exc:
            # 不把第三方库和 OOXML 内部结构暴露给最终用户，详细堆栈由 Worker 日志记录。
            raise DocumentParseError(
                code=DocumentParseErrorCode.DOCX_INVALID,
                message="Word 文档损坏或不是有效的 DOCX 文件",
            ) from exc

        if not sections:
            raise DocumentParseError(
                code=DocumentParseErrorCode.NO_EXTRACTABLE_TEXT,
                message="Word 文档中没有可提取文本，图片中的文字暂不支持识别",
            )
        return ParsedDocument(
            sections=tuple(sections),
            page_count=0,
            parser_version=self.parser_version,
        )

    def _validate_archive(self, content: bytes) -> None:
        """仅检查 ZIP 元数据和必要包部件，不把不可信文件解压到磁盘。"""
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if not REQUIRED_PACKAGE_PARTS.issubset(names):
                    raise self._invalid_document_error()
                if len(entries) > self.max_archive_entries:
                    raise self._unsafe_archive_error("压缩包条目数量超过限制")

                total_uncompressed = 0
                for entry in entries:
                    normalized_name = entry.filename.replace("\\", "/").lower()
                    if entry.flag_bits & 0x1:
                        raise self._unsafe_archive_error("不支持加密的 DOCX 压缩包")
                    if normalized_name.endswith(MACRO_PART_SUFFIX):
                        raise self._unsafe_archive_error("DOCX 中包含宏载荷")
                    if entry.file_size > self.max_entry_uncompressed_bytes:
                        raise self._unsafe_archive_error("单个压缩条目解压后过大")

                    total_uncompressed += entry.file_size
                    if total_uncompressed > self.max_uncompressed_bytes:
                        raise self._unsafe_archive_error("压缩包解压后总大小超过限制")
                    if self._compression_ratio(entry.file_size, entry.compress_size) > (
                        self.max_compression_ratio
                    ):
                        raise self._unsafe_archive_error("压缩率异常，疑似压缩炸弹")

                # 宏启用文档即使没有标准宏部件，也会在 Content Types 中留下声明。
                content_types = archive.read("[Content_Types].xml").lower()
                if b"macroenabled" in content_types:
                    raise self._unsafe_archive_error("DOCX 声明为宏启用文档")
        except DocumentParseError:
            raise
        except (
            BadZipFile,
            LargeZipFile,
            KeyError,
            RuntimeError,
            NotImplementedError,
        ) as exc:
            raise self._invalid_document_error() from exc

    def _parse_body(self, document: DocxDocument) -> list[ParsedSection]:
        """遍历顶层正文块，并把相邻同类内容组合成可继续 Token 分块的来源段。"""
        sections: list[ParsedSection] = []
        headings: dict[int, str] = {}
        block_indices: defaultdict[str, int] = defaultdict(int)
        starts_new_group = True

        # python-docx 1.2 的 iter_inner_content 能保持段落与表格交错顺序。
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                heading = self._read_heading(block)
                if heading is not None:
                    level, title = heading
                    headings = {
                        existing_level: existing_title
                        for existing_level, existing_title in headings.items()
                        if existing_level < level
                    }
                    headings[level] = title
                    starts_new_group = True
                    continue

                paragraph = self._normalize_text(block.text)
                if not paragraph:
                    continue
                block_kind: Literal["paragraph", "list", "table"] = (
                    "list" if self._is_list(block) else "paragraph"
                )
                content = f"- {paragraph}" if block_kind == "list" else paragraph
            elif isinstance(block, Table):
                content = self._table_to_markdown(block)
                if not content:
                    continue
                block_kind = "table"
            else:  # pragma: no cover - 当前 python-docx 契约只返回 Paragraph 或 Table。
                continue

            heading_path = tuple(headings[level] for level in sorted(headings))
            previous = sections[-1] if sections else None
            previous_locator = previous.source_locator if previous is not None else None
            can_merge = (
                not starts_new_group
                and block_kind != "table"
                and previous is not None
                and previous.heading_path == heading_path
                and isinstance(previous_locator, SectionSourceLocator)
                and previous_locator.block_kind == block_kind
            )
            if can_merge:
                # 相邻正文用空行保留段落边界；列表仅换行，保持连续 Markdown 列表语义。
                separator = "\n" if block_kind == "list" else "\n\n"
                sections[-1] = ParsedSection(
                    content=f"{previous.content}{separator}{content}",
                    heading_path=previous.heading_path,
                    source_locator=previous_locator,
                )
                continue

            block_index = block_indices[block_kind]
            block_indices[block_kind] += 1
            sections.append(
                ParsedSection(
                    content=content,
                    heading_path=heading_path,
                    source_locator=SectionSourceLocator(
                        heading_path=heading_path,
                        block_kind=block_kind,
                        block_index=block_index,
                    ),
                )
            )
            starts_new_group = False
        return sections

    @staticmethod
    def _read_heading(paragraph: Paragraph) -> tuple[int, str] | None:
        """同时识别内置英文/中文标题名和稳定的 HeadingN 样式 ID。"""
        title = DocxParser._normalize_text(paragraph.text)
        if not title:
            return None
        style = paragraph.style
        style_name = (style.name or "").strip() if style is not None else ""
        style_id = (style.style_id or "").strip() if style is not None else ""
        match = HEADING_STYLE_PATTERN.match(
            style_name
        ) or HEADING_STYLE_ID_PATTERN.match(style_id)
        return (int(match.group(1)), title) if match is not None else None

    @staticmethod
    def _is_list(paragraph: Paragraph) -> bool:
        """优先识别编号属性，并兼容 Word 内置和常见本地化列表样式名。"""
        properties = paragraph._p.pPr  # noqa: SLF001 - OOXML 编号尚无公开高层 API。
        if properties is not None and properties.numPr is not None:
            return True
        style = paragraph.style
        style_name = (style.name or "").lower() if style is not None else ""
        style_id = (style.style_id or "").lower() if style is not None else ""
        return any(
            marker in style_name or marker in style_id for marker in LIST_STYLE_MARKERS
        )

    @staticmethod
    def _table_to_markdown(table: Table) -> str:
        """把表格转为 LLM 易读的 Markdown，并把第一行作为表头。"""
        rows = [
            [DocxParser._escape_table_cell(cell.text) for cell in row.cells]
            for row in table.rows
        ]
        if not rows or not any(cell for row in rows for cell in row):
            return ""
        width = max(len(row) for row in rows)
        normalized_rows = [row + [""] * (width - len(row)) for row in rows]
        header = normalized_rows[0]
        lines = [
            f"| {' | '.join(header)} |",
            f"| {' | '.join('---' for _ in range(width))} |",
        ]
        lines.extend(f"| {' | '.join(row)} |" for row in normalized_rows[1:])
        return "\n".join(lines)

    @staticmethod
    def _escape_table_cell(value: str) -> str:
        """压缩单元格空白，并转义会破坏 Markdown 列边界的竖线。"""
        lines = [DocxParser._normalize_text(line) for line in value.splitlines()]
        content = "<br>".join(line for line in lines if line)
        return content.replace("|", r"\|")

    @staticmethod
    def _normalize_text(value: str) -> str:
        """保留可读正文，同时统一不换行空格和连续横向空白。"""
        return re.sub(r"[\t\u00a0 ]+", " ", value).strip()

    @staticmethod
    def _compression_ratio(uncompressed_size: int, compressed_size: int) -> float:
        """空条目比例为零；非空但压缩后为零视为无限压缩率。"""
        if uncompressed_size == 0:
            return 0.0
        if compressed_size == 0:
            return float("inf")
        return uncompressed_size / compressed_size

    @staticmethod
    def _invalid_document_error() -> DocumentParseError:
        """构造不会泄露 OOXML 包细节的损坏文件错误。"""
        return DocumentParseError(
            code=DocumentParseErrorCode.DOCX_INVALID,
            message="Word 文档损坏或不是有效的 DOCX 文件",
        )

    @staticmethod
    def _unsafe_archive_error(reason: str) -> DocumentParseError:
        """构造包含安全原因、可供失败状态展示的不可重试错误。"""
        return DocumentParseError(
            code=DocumentParseErrorCode.OFFICE_ARCHIVE_UNSAFE,
            message=f"Word 文档未通过安全检查：{reason}",
        )
