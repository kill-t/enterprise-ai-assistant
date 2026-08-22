from pathlib import Path

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import UnsupportedDocumentTypeError
from app.modules.knowledge.processing.docx_parser import DocxParser
from app.modules.knowledge.processing.markdown_parser import MarkdownParser
from app.modules.knowledge.processing.pdf_parser import PdfParser
from app.modules.knowledge.processing.text_parser import TextParser
from app.modules.knowledge.processing.xlsx_parser import XlsxParser


class DocumentParserRegistry:
    """根据文件扩展名选择统一 Parser，隐藏具体第三方实现。"""

    def __init__(self) -> None:
        """注册当前允许的 PDF、TXT、Markdown、DOCX 和 XLSX Parser。"""
        text_parser = TextParser()
        markdown_parser = MarkdownParser()
        self._parsers: dict[str, BaseDocumentParser] = {
            ".pdf": PdfParser(),
            ".txt": text_parser,
            ".md": markdown_parser,
            ".markdown": markdown_parser,
            ".docx": DocxParser(),
            ".xlsx": XlsxParser(),
        }

    def get_parser(self, filename: str) -> BaseDocumentParser:
        """按不区分大小写的扩展名返回 Parser，未知类型明确失败。"""
        parser = self._parsers.get(Path(filename).suffix.lower())
        if parser is None:
            raise UnsupportedDocumentTypeError()
        return parser
