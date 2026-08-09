import asyncio
from typing import BinaryIO

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection


def normalize_text(text: str) -> str:
    """统一换行符并移除正文首尾无意义空白。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


class TextParser(BaseDocumentParser):
    """解析 UTF-8 TXT，并为 Markdown Parser 复用安全解码逻辑。"""

    parser_version = "text-v1"

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """解码 TXT，并输出一个不带页码和标题的来源段。"""
        text = await self._read_utf8_text(stream)
        return ParsedDocument(
            sections=(ParsedSection(content=text),),
            page_count=0,
            parser_version=self.parser_version,
        )

    async def _read_utf8_text(self, stream: BinaryIO) -> str:
        """在线程池读取本地临时流，并转换 UTF-8 编码与空文本错误。"""
        try:
            stream.seek(0)
            content = await asyncio.to_thread(stream.read)
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentParseError(
                code=DocumentParseErrorCode.INVALID_TEXT_ENCODING,
                message="文本文件必须使用 UTF-8 编码",
            ) from exc

        normalized = normalize_text(text)
        if not normalized:
            raise DocumentParseError(
                code=DocumentParseErrorCode.NO_EXTRACTABLE_TEXT,
                message="文档中没有可提取文本",
            )
        return normalized
