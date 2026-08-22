import asyncio
from io import BytesIO
from typing import Protocol
from typing import BinaryIO

from pypdf import PdfReader

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.processing.text_parser import normalize_text


class PdfOcrPageParserProtocol(Protocol):
    """描述 PdfParser 所需的最小扫描页 OCR 能力，便于测试替换。"""

    async def parse_page(
        self,
        pdf_content: bytes,
        *,
        page_index: int,
        page_number: int,
    ) -> tuple[ParsedSection, ...]:
        """渲染并识别一张扫描 PDF 页面。"""
        ...


class PdfParser(BaseDocumentParser):
    """优先提取 PDF 文本层，并仅对空文本页面执行可选 OCR 回退。"""

    parser_version = "pypdf-v1"

    def __init__(
        self,
        *,
        ocr_page_parser: PdfOcrPageParserProtocol | None = None,
        max_ocr_pages: int = 50,
    ) -> None:
        """保存可替换 OCR 页解析器和单文档扫描页数量上限。"""
        if max_ocr_pages <= 0:
            raise ValueError("单个 PDF 的 OCR 页数上限必须大于零")
        self.ocr_page_parser = ocr_page_parser
        self.max_ocr_pages = max_ocr_pages

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """在线程池提取文本层，再按原页序异步补齐扫描页面。"""
        stream.seek(0)
        pdf_content = stream.read()
        page_texts = await asyncio.to_thread(self._extract_page_texts, pdf_content)
        empty_page_count = sum(not content for content in page_texts)
        if self.ocr_page_parser is not None and empty_page_count > self.max_ocr_pages:
            raise DocumentParseError(
                code=DocumentParseErrorCode.OCR_LIMIT_EXCEEDED,
                message="PDF 中需要 OCR 的页面数量超过限制",
            )

        sections: list[ParsedSection] = []
        ocr_attempted = False
        for page_index, content in enumerate(page_texts):
            page_number = page_index + 1
            if content:
                sections.append(
                    ParsedSection(content=content, page_number=page_number)
                )
                continue
            if self.ocr_page_parser is None:
                continue
            ocr_attempted = True
            try:
                sections.extend(
                    await self.ocr_page_parser.parse_page(
                        pdf_content,
                        page_index=page_index,
                        page_number=page_number,
                    )
                )
            except DocumentParseError as exc:
                # 空白装饰页或模糊页可以跳过；如果整份文档都无可靠文字，会在末尾失败。
                if exc.code != DocumentParseErrorCode.OCR_NO_RELIABLE_TEXT:
                    raise

        if not sections:
            if ocr_attempted:
                raise DocumentParseError(
                    code=DocumentParseErrorCode.OCR_NO_RELIABLE_TEXT,
                    message="PDF 扫描页面中没有达到置信度要求的可识别文字",
                )
            raise DocumentParseError(
                code=DocumentParseErrorCode.NO_EXTRACTABLE_TEXT,
                message="PDF 中没有可提取文本，扫描件需要 OCR",
            )
        return ParsedDocument(
            sections=tuple(sections),
            page_count=len(page_texts),
            parser_version=(
                "pypdf+ocr-v2" if self.ocr_page_parser is not None else self.parser_version
            ),
        )

    def _extract_page_texts(self, pdf_content: bytes) -> tuple[str, ...]:
        """同步读取文本层并返回与 PDF 页序严格对齐的结果。"""
        try:
            reader = PdfReader(BytesIO(pdf_content))
            if reader.is_encrypted:
                raise DocumentParseError(
                    code=DocumentParseErrorCode.PDF_ENCRYPTED,
                    message="暂不支持加密 PDF",
                )

            return tuple(
                normalize_text(page.extract_text() or "") for page in reader.pages
            )
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                code=DocumentParseErrorCode.PDF_INVALID,
                message="PDF 文件损坏或格式无效",
            ) from exc
