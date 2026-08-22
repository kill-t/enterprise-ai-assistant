from pathlib import Path

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import UnsupportedDocumentTypeError
from app.modules.knowledge.processing.docx_parser import DocxParser
from app.modules.knowledge.processing.markdown_parser import MarkdownParser
from app.modules.knowledge.processing.image_ocr_parser import ImageOcrParser
from app.modules.knowledge.processing.ocr_provider import (
    BaseOcrProvider,
    RapidOcrProvider,
)
from app.modules.knowledge.processing.pdf_parser import PdfParser
from app.modules.knowledge.processing.pdf_ocr_page_parser import PdfOcrPageParser
from app.modules.knowledge.processing.text_parser import TextParser
from app.modules.knowledge.processing.xlsx_parser import XlsxParser


class DocumentParserRegistry:
    """根据文件扩展名选择统一 Parser，隐藏具体第三方实现。"""

    def __init__(
        self,
        *,
        ocr_provider: BaseOcrProvider | None = None,
        ocr_min_confidence: float = 0.65,
        ocr_pdf_dpi: int = 200,
        ocr_max_pdf_pages: int = 50,
        ocr_max_image_pixels: int = 25_000_000,
        ocr_max_image_width: int = 10_000,
        ocr_max_image_height: int = 10_000,
        ocr_max_lines_per_section: int = 20,
        ocr_max_characters_per_section: int = 500,
    ) -> None:
        """注册文档 Parser，并让图片与扫描 PDF 复用同一个 OCR Provider。"""
        text_parser = TextParser()
        markdown_parser = MarkdownParser()
        image_parser = ImageOcrParser(
            provider=ocr_provider or RapidOcrProvider(),
            min_confidence=ocr_min_confidence,
            max_image_pixels=ocr_max_image_pixels,
            max_image_width=ocr_max_image_width,
            max_image_height=ocr_max_image_height,
            max_lines_per_section=ocr_max_lines_per_section,
            max_characters_per_section=ocr_max_characters_per_section,
        )
        pdf_ocr_page_parser = PdfOcrPageParser(
            image_parser=image_parser,
            dpi=ocr_pdf_dpi,
            max_rendered_pixels=ocr_max_image_pixels,
        )
        self._parsers: dict[str, BaseDocumentParser] = {
            ".pdf": PdfParser(
                ocr_page_parser=pdf_ocr_page_parser,
                max_ocr_pages=ocr_max_pdf_pages,
            ),
            ".txt": text_parser,
            ".md": markdown_parser,
            ".markdown": markdown_parser,
            ".docx": DocxParser(),
            ".xlsx": XlsxParser(),
            ".png": image_parser,
            ".jpg": image_parser,
            ".jpeg": image_parser,
        }

    def get_parser(self, filename: str) -> BaseDocumentParser:
        """按不区分大小写的扩展名返回 Parser，未知类型明确失败。"""
        parser = self._parsers.get(Path(filename).suffix.lower())
        if parser is None:
            raise UnsupportedDocumentTypeError()
        return parser
