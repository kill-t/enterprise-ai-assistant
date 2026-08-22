"""安全解码 PNG/JPEG，并把 OCR 行组织成可追溯的来源段。"""

import warnings
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.modules.knowledge.processing.base import BaseDocumentParser
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.ocr_provider import (
    BaseOcrProvider,
    OcrImage,
    OcrProviderError,
    OcrResult,
    OcrTextLine,
)
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.processing.text_parser import normalize_text
from app.modules.knowledge.source_locator import (
    NormalizedBoundingBox,
    OcrRegionSourceLocator,
)


class ImageOcrParser(BaseDocumentParser):
    """限制图片解码资源、过滤低置信度结果，并生成区域级引用。"""

    parser_version = "image-ocr-v1"
    supported_formats = frozenset({"PNG", "JPEG"})

    def __init__(
        self,
        *,
        provider: BaseOcrProvider,
        min_confidence: float = 0.65,
        max_image_pixels: int = 25_000_000,
        max_image_width: int = 10_000,
        max_image_height: int = 10_000,
        max_lines_per_section: int = 20,
        max_characters_per_section: int = 500,
    ) -> None:
        """保存 OCR 可靠性、解码资源和区域预分块边界。"""
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("OCR 最低置信度必须位于 0 到 1")
        limits = (
            max_image_pixels,
            max_image_width,
            max_image_height,
            max_lines_per_section,
            max_characters_per_section,
        )
        if any(limit <= 0 for limit in limits):
            raise ValueError("OCR 图片和预分块上限必须大于零")
        self.provider = provider
        self.min_confidence = min_confidence
        self.max_image_pixels = max_image_pixels
        self.max_image_width = max_image_width
        self.max_image_height = max_image_height
        self.max_lines_per_section = max_lines_per_section
        self.max_characters_per_section = max_characters_per_section

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """解析独立图片；图片没有 PDF 页码，因此引用只展示区域与置信度。"""
        stream.seek(0)
        content = stream.read()
        sections = await self.parse_image_content(content, page_number=None)
        return ParsedDocument(
            sections=sections,
            page_count=0,
            parser_version=self.parser_version,
        )

    async def parse_image_content(
        self,
        content: bytes,
        *,
        page_number: int | None,
    ) -> tuple[ParsedSection, ...]:
        """复用同一安全入口处理独立图片或扫描 PDF 渲染页。"""
        image = self._decode_and_normalize(content)
        try:
            result = await self.provider.recognize(image)
        except OcrProviderError as exc:
            raise DocumentParseError(
                code=DocumentParseErrorCode.OCR_ENGINE_FAILED,
                message="OCR 引擎调用失败，请稍后重试",
            ) from exc

        reliable_lines = self._reliable_lines(result)
        if not reliable_lines:
            raise DocumentParseError(
                code=DocumentParseErrorCode.OCR_NO_RELIABLE_TEXT,
                message="图片中没有达到置信度要求的可识别文字",
            )
        return self._build_sections(
            reliable_lines,
            page_number=page_number,
            engine=result.engine,
        )

    def _decode_and_normalize(self, content: bytes) -> OcrImage:
        """先检查尺寸再完整解码，阻止小压缩文件展开为超大像素图。"""
        if not content:
            raise self._invalid_image_error()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as source:
                    if source.format not in self.supported_formats:
                        raise self._invalid_image_error()
                    self._validate_dimensions(*source.size)
                    source.load()
                    transposed = ImageOps.exif_transpose(source)
                    self._validate_dimensions(*transposed.size)
                    normalized = self._to_rgb_on_white(transposed)
                    output = BytesIO()
                    normalized.save(output, format="PNG", optimize=False)
                    return OcrImage(
                        content=output.getvalue(),
                        width=normalized.width,
                        height=normalized.height,
                    )
        except DocumentParseError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise self._limit_error("图片解码后的像素数量超过限制") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise self._invalid_image_error() from exc

    def _validate_dimensions(self, width: int, height: int) -> None:
        """限制宽、高和总像素，且在 Pillow 分配完整像素前执行。"""
        if width <= 0 or height <= 0:
            raise self._invalid_image_error()
        if (
            width > self.max_image_width
            or height > self.max_image_height
            or width * height > self.max_image_pixels
        ):
            raise self._limit_error("图片尺寸或像素数量超过 OCR 限制")

    @staticmethod
    def _to_rgb_on_white(image: Image.Image) -> Image.Image:
        """把透明区域合成白底 RGB，避免透明 PNG 被转成黑底影响文字识别。"""
        if "A" not in image.getbands():
            return image.convert("RGB")
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, color=(255, 255, 255, 255))
        white.alpha_composite(rgba)
        return white.convert("RGB")

    def _reliable_lines(self, result: OcrResult) -> list[OcrTextLine]:
        """清理单行换行、过滤低置信度，再按从上到下和从左到右排序。"""
        lines: list[OcrTextLine] = []
        for line in result.lines:
            content = normalize_text(line.text).replace("\n", " ").strip()
            if content and line.confidence >= self.min_confidence:
                lines.append(
                    OcrTextLine(
                        text=content,
                        confidence=line.confidence,
                        bounding_box=line.bounding_box,
                    )
                )
        return sorted(
            lines,
            key=lambda line: (
                line.bounding_box.y_min,
                line.bounding_box.x_min,
            ),
        )

    def _build_sections(
        self,
        lines: list[OcrTextLine],
        *,
        page_number: int | None,
        engine: str,
    ) -> tuple[ParsedSection, ...]:
        """按完整 OCR 行预分块，并用各行外接矩形作为可审计区域。"""
        groups: list[list[OcrTextLine]] = []
        current: list[OcrTextLine] = []
        current_characters = 0
        for line in lines:
            separator_size = 1 if current else 0
            would_exceed = current and (
                len(current) >= self.max_lines_per_section
                or current_characters + separator_size + len(line.text)
                > self.max_characters_per_section
            )
            if would_exceed:
                groups.append(current)
                current = []
                current_characters = 0
                separator_size = 0
            current.append(line)
            current_characters += separator_size + len(line.text)
        if current:
            groups.append(current)

        return tuple(
            ParsedSection(
                content="\n".join(line.text for line in group),
                page_number=page_number,
                source_locator=OcrRegionSourceLocator(
                    page_number=page_number,
                    bounding_box=self._union_box(group),
                    # 使用组内最低分作为保守置信度，不用平均值掩盖某一行的风险。
                    confidence=min(line.confidence for line in group),
                    engine=engine,
                ),
            )
            for group in groups
        )

    @staticmethod
    def _union_box(lines: list[OcrTextLine]) -> NormalizedBoundingBox:
        """计算一组识别行的最小归一化外接矩形。"""
        return NormalizedBoundingBox(
            x_min=min(line.bounding_box.x_min for line in lines),
            y_min=min(line.bounding_box.y_min for line in lines),
            x_max=max(line.bounding_box.x_max for line in lines),
            y_max=max(line.bounding_box.y_max for line in lines),
        )

    @staticmethod
    def _invalid_image_error() -> DocumentParseError:
        """构造不会泄露 Pillow 内部实现的图片格式错误。"""
        return DocumentParseError(
            code=DocumentParseErrorCode.IMAGE_INVALID,
            message="图片损坏或不是有效的 PNG/JPEG 文件",
        )

    @staticmethod
    def _limit_error(message: str) -> DocumentParseError:
        """构造图片或 OCR 资源超过边界的稳定错误。"""
        return DocumentParseError(
            code=DocumentParseErrorCode.OCR_LIMIT_EXCEEDED,
            message=message,
        )
