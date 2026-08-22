"""把扫描 PDF 的指定页面安全渲染为图片，并复用图片 OCR Parser。"""

import asyncio
import math

import pymupdf

from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.image_ocr_parser import ImageOcrParser
from app.modules.knowledge.processing.schemas import ParsedSection


class PdfOcrPageParser:
    """只渲染 pypdf 未提取到文本的页面，避免普通 PDF 重复 OCR。"""

    def __init__(
        self,
        *,
        image_parser: ImageOcrParser,
        dpi: int = 200,
        max_rendered_pixels: int = 25_000_000,
    ) -> None:
        """保存渲染清晰度和渲染前像素上限。"""
        if dpi < 72 or dpi > 300:
            raise ValueError("PDF OCR DPI 必须位于 72 到 300")
        if max_rendered_pixels <= 0:
            raise ValueError("PDF OCR 渲染像素上限必须大于零")
        self.image_parser = image_parser
        self.dpi = dpi
        self.max_rendered_pixels = max_rendered_pixels

    async def parse_page(
        self,
        pdf_content: bytes,
        *,
        page_index: int,
        page_number: int,
    ) -> tuple[ParsedSection, ...]:
        """在线程池渲染单页，再异步调用统一图片 OCR 流程。"""
        png_content = await asyncio.to_thread(
            self._render_page_sync,
            pdf_content,
            page_index,
        )
        return await self.image_parser.parse_image_content(
            png_content,
            page_number=page_number,
        )

    def _render_page_sync(self, pdf_content: bytes, page_index: int) -> bytes:
        """在创建 Pixmap 前估算像素，拒绝异常页面尺寸。"""
        document = None
        try:
            document = pymupdf.open(stream=pdf_content, filetype="pdf")
            if page_index < 0 or page_index >= document.page_count:
                raise ValueError("PDF 页索引越界")
            page = document.load_page(page_index)
            scale = self.dpi / 72
            width = math.ceil(page.rect.width * scale)
            height = math.ceil(page.rect.height * scale)
            if width <= 0 or height <= 0 or width * height > self.max_rendered_pixels:
                raise DocumentParseError(
                    code=DocumentParseErrorCode.OCR_LIMIT_EXCEEDED,
                    message="PDF 页面渲染后的像素数量超过 OCR 限制",
                )
            # alpha=False 可减少约四分之一像素内存，并为 OCR 生成白色背景。
            pixmap = page.get_pixmap(dpi=self.dpi, alpha=False)
            return pixmap.tobytes("png")
        except DocumentParseError:
            raise
        except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
            raise DocumentParseError(
                code=DocumentParseErrorCode.PDF_INVALID,
                message="PDF 页面无法渲染或格式无效",
            ) from exc
        finally:
            if document is not None:
                document.close()
