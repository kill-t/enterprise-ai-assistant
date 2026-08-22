import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
import pymupdf
from PIL import Image

from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.image_ocr_parser import ImageOcrParser
from app.modules.knowledge.processing.ocr_provider import (
    BaseOcrProvider,
    OcrImage,
    OcrResult,
    OcrTextLine,
    RapidOcrProvider,
)
from app.modules.knowledge.processing.pdf_parser import PdfParser
from app.modules.knowledge.processing.pdf_ocr_page_parser import PdfOcrPageParser
from app.modules.knowledge.processing.schemas import ParsedSection
from app.modules.knowledge.source_locator import (
    NormalizedBoundingBox,
    OcrRegionSourceLocator,
)


class StubOcrProvider(BaseOcrProvider):
    """返回指定 OCR 行并记录输入，避免单元测试依赖真实模型推理。"""

    def __init__(self, lines: tuple[OcrTextLine, ...]) -> None:
        """保存本次测试预设的识别结果。"""
        self.lines = lines
        self.images: list[OcrImage] = []

    async def recognize(self, image: OcrImage) -> OcrResult:
        """记录规范化图片并返回稳定结果。"""
        self.images.append(image)
        return OcrResult(lines=self.lines, engine="stub-ocr-v1")


class StubPdfOcrPageParser:
    """模拟扫描 PDF 页面的渲染与 OCR，不依赖真实 PDF 图像。"""

    def __init__(self) -> None:
        """记录被回退处理的零基页索引。"""
        self.page_indexes: list[int] = []

    async def parse_page(
        self,
        pdf_content: bytes,
        *,
        page_index: int,
        page_number: int,
    ) -> tuple[ParsedSection, ...]:
        """为指定页返回一段可追溯 OCR 文本。"""
        self.page_indexes.append(page_index)
        return (
            ParsedSection(
                content=f"扫描第{page_number}页",
                page_number=page_number,
                source_locator=OcrRegionSourceLocator(
                    page_number=page_number,
                    bounding_box=NormalizedBoundingBox(
                        x_min=0.1,
                        y_min=0.2,
                        x_max=0.8,
                        y_max=0.4,
                    ),
                    confidence=0.92,
                    engine="stub-ocr-v1",
                ),
            ),
        )


def test_image_ocr_parser_sorts_lines_filters_confidence_and_builds_regions() -> None:
    """OCR 应按阅读顺序输出，并过滤低置信度文本且保留区域引用。"""
    provider = StubOcrProvider(
        lines=(
            build_ocr_line("第二行", confidence=0.95, box=(0.1, 0.5, 0.8, 0.6)),
            build_ocr_line("噪声", confidence=0.4, box=(0.1, 0.3, 0.3, 0.4)),
            build_ocr_line("第一行", confidence=0.9, box=(0.2, 0.1, 0.7, 0.2)),
        )
    )
    parser = ImageOcrParser(
        provider=provider,
        min_confidence=0.7,
        max_lines_per_section=20,
        max_characters_per_section=500,
    )

    parsed = asyncio.run(parser.parse(build_png(width=120, height=80)))

    assert [section.content for section in parsed.sections] == ["第一行\n第二行"]
    locator = parsed.sections[0].source_locator
    assert isinstance(locator, OcrRegionSourceLocator)
    assert locator.page_number is None
    assert locator.bounding_box == NormalizedBoundingBox(
        x_min=0.1,
        y_min=0.1,
        x_max=0.8,
        y_max=0.6,
    )
    assert locator.confidence == pytest.approx(0.9)
    assert locator.engine == "stub-ocr-v1"
    assert provider.images[0].width == 120
    assert provider.images[0].height == 80
    assert parsed.parser_version == "image-ocr-v1"


def test_image_ocr_parser_splits_large_results_without_splitting_a_line() -> None:
    """OCR 预分块应保留完整识别行，避免后续 Chunk 引用整张图片。"""
    provider = StubOcrProvider(
        lines=tuple(
            build_ocr_line(
                f"第{index}行内容",
                confidence=0.9,
                box=(0.1, index / 10, 0.9, index / 10 + 0.05),
            )
            for index in range(1, 5)
        )
    )
    parser = ImageOcrParser(
        provider=provider,
        max_lines_per_section=2,
        max_characters_per_section=100,
    )

    parsed = asyncio.run(parser.parse(build_png()))

    assert [section.content for section in parsed.sections] == [
        "第1行内容\n第2行内容",
        "第3行内容\n第4行内容",
    ]
    assert all(
        isinstance(section.source_locator, OcrRegionSourceLocator)
        for section in parsed.sections
    )


def test_image_ocr_parser_rejects_low_confidence_and_oversized_images() -> None:
    """没有可靠文字或解码后像素过大时不能生成误导性索引。"""
    low_confidence_provider = StubOcrProvider(
        lines=(build_ocr_line("模糊文字", confidence=0.3),)
    )
    low_confidence_parser = ImageOcrParser(
        provider=low_confidence_provider,
        min_confidence=0.7,
    )

    error = run_parse_error(low_confidence_parser.parse(build_png()))

    assert error.code == DocumentParseErrorCode.OCR_NO_RELIABLE_TEXT

    oversized_provider = StubOcrProvider(lines=())
    oversized_parser = ImageOcrParser(
        provider=oversized_provider,
        max_image_pixels=99,
    )

    error = run_parse_error(
        oversized_parser.parse(build_png(width=10, height=10))
    )

    assert error.code == DocumentParseErrorCode.OCR_LIMIT_EXCEEDED
    assert oversized_provider.images == []


def test_pdf_parser_uses_ocr_only_for_pages_without_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """混合 PDF 的文本页继续用 pypdf，只有空文本页才进入 OCR 回退。"""
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[FakePdfPage("第一页文本"), FakePdfPage(None), FakePdfPage("第三页文本")],
    )
    monkeypatch.setattr(
        "app.modules.knowledge.processing.pdf_parser.PdfReader",
        lambda stream: reader,
    )
    ocr_page_parser = StubPdfOcrPageParser()
    parser = PdfParser(ocr_page_parser=ocr_page_parser)

    parsed = asyncio.run(parser.parse(BytesIO(b"mixed pdf")))

    assert [section.content for section in parsed.sections] == [
        "第一页文本",
        "扫描第2页",
        "第三页文本",
    ]
    assert [section.page_number for section in parsed.sections] == [1, 2, 3]
    assert ocr_page_parser.page_indexes == [1]
    assert parsed.parser_version == "pypdf+ocr-v2"


def test_rapidocr_provider_normalizes_engine_coordinates() -> None:
    """RapidOCR 第三方输出必须转为稳定的归一化内部契约。"""
    engine = lambda image_bytes: SimpleNamespace(  # noqa: E731 - 简洁模拟可调用引擎。
        txts=["申请条件"],
        scores=[0.88],
        boxes=[[[20, 10], [180, 10], [180, 30], [20, 30]]],
    )
    provider = RapidOcrProvider(engine_factory=lambda: engine)

    result = asyncio.run(
        provider.recognize(
            OcrImage(
                content=build_png(width=200, height=100).getvalue(),
                width=200,
                height=100,
            )
        )
    )

    assert result.engine.startswith("rapidocr")
    assert result.lines[0].text == "申请条件"
    assert result.lines[0].confidence == pytest.approx(0.88)
    assert result.lines[0].bounding_box == NormalizedBoundingBox(
        x_min=0.1,
        y_min=0.1,
        x_max=0.9,
        y_max=0.3,
    )


def test_scanned_pdf_page_is_really_rendered_before_ocr() -> None:
    """真实图片型 PDF 应由 PyMuPDF 渲染，再复用图片 OCR 与页码引用。"""
    provider = StubOcrProvider(
        lines=(build_ocr_line("扫描页正文", confidence=0.93),)
    )
    image_parser = ImageOcrParser(provider=provider)
    page_parser = PdfOcrPageParser(
        image_parser=image_parser,
        dpi=120,
        max_rendered_pixels=2_000_000,
    )
    parser = PdfParser(ocr_page_parser=page_parser)

    parsed = asyncio.run(parser.parse(build_image_only_pdf()))

    assert parsed.page_count == 1
    assert parsed.sections[0].content == "扫描页正文"
    locator = parsed.sections[0].source_locator
    assert isinstance(locator, OcrRegionSourceLocator)
    assert locator.page_number == 1
    assert provider.images[0].width == 400
    assert provider.images[0].height == 200


def build_ocr_line(
    text: str,
    *,
    confidence: float,
    box: tuple[float, float, float, float] = (0.1, 0.1, 0.9, 0.2),
) -> OcrTextLine:
    """构造已经归一化的 OCR 行结果。"""
    return OcrTextLine(
        text=text,
        confidence=confidence,
        bounding_box=NormalizedBoundingBox(
            x_min=box[0],
            y_min=box[1],
            x_max=box[2],
            y_max=box[3],
        ),
    )


def build_png(*, width: int = 100, height: int = 100) -> BytesIO:
    """生成无需磁盘临时文件的合法 RGB PNG。"""
    stream = BytesIO()
    Image.new("RGB", (width, height), color="white").save(stream, format="PNG")
    stream.seek(0)
    return stream


def build_image_only_pdf() -> BytesIO:
    """构造没有文本层、仅含一张图片的内存 PDF。"""
    document = pymupdf.open()
    try:
        page = document.new_page(width=240, height=120)
        page.insert_image(page.rect, stream=build_png(width=400, height=200).getvalue())
        content = document.tobytes()
    finally:
        document.close()
    return BytesIO(content)


class FakePdfPage:
    """模拟 pypdf 页面，只提供文本提取接口。"""

    def __init__(self, text: str | None) -> None:
        """保存该页的文本层。"""
        self.text = text

    def extract_text(self) -> str | None:
        """返回预设文本。"""
        return self.text


def run_parse_error(awaitable) -> DocumentParseError:
    """运行解析并返回预期的稳定错误。"""

    async def scenario() -> DocumentParseError:
        """捕获领域解析错误，未失败时主动中止测试。"""
        try:
            await awaitable
        except DocumentParseError as exc:
            return exc
        raise AssertionError("解析操作必须失败")

    return asyncio.run(scenario())
