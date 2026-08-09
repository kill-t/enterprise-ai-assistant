import asyncio
from typing import BinaryIO

from pypdf import PdfReader

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.processing.text_parser import normalize_text


class PdfParser(BaseDocumentParser):
    """使用 pypdf 按页提取文本，并保留从 1 开始的真实页码。"""

    parser_version = "pypdf-v1"

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """在线程池执行同步 PDF 解析，避免阻塞 FastAPI 或 Worker 事件循环。"""
        return await asyncio.to_thread(self._parse_sync, stream)

    def _parse_sync(self, stream: BinaryIO) -> ParsedDocument:
        """读取 PDF、拒绝加密文件，并将非空页面转换为来源段。"""
        try:
            stream.seek(0)
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise DocumentParseError(
                    code=DocumentParseErrorCode.PDF_ENCRYPTED,
                    message="暂不支持加密 PDF",
                )

            sections: list[ParsedSection] = []
            for page_number, page in enumerate(reader.pages, start=1):
                content = normalize_text(page.extract_text() or "")
                if content:
                    sections.append(
                        ParsedSection(
                            content=content,
                            page_number=page_number,
                        )
                    )

            if not sections:
                raise DocumentParseError(
                    code=DocumentParseErrorCode.NO_EXTRACTABLE_TEXT,
                    message="PDF 中没有可提取文本，扫描件需要 OCR",
                )
            return ParsedDocument(
                sections=tuple(sections),
                page_count=len(reader.pages),
                parser_version=self.parser_version,
            )
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                code=DocumentParseErrorCode.PDF_INVALID,
                message="PDF 文件损坏或格式无效",
            ) from exc
