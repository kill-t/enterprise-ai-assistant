import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from app.infrastructure.llm.token_counter import TokenCounter
from app.modules.knowledge.processing.chunker import TokenAwareChunker
from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
    UnsupportedDocumentTypeError,
)
from app.modules.knowledge.processing.docx_parser import DocxParser
from app.modules.knowledge.processing.image_ocr_parser import ImageOcrParser
from app.modules.knowledge.processing.markdown_parser import MarkdownParser
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.processing.pdf_parser import PdfParser
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.processing.text_parser import TextParser
from app.modules.knowledge.processing.xlsx_parser import XlsxParser


class CharacterTokenCounter:
    """使用字符数模拟 token，便于精确断言分块预算。"""

    def count(self, text: str) -> int:
        """将每个字符视为一个 token。"""
        return len(text)


def test_text_parser_decodes_utf8_bom_and_normalizes_line_endings() -> None:
    """TXT 解析器应识别 UTF-8 BOM，并把 Windows 换行统一为 LF。"""
    parser = TextParser()

    parsed = asyncio.run(parser.parse(BytesIO(b"\xef\xbb\xbfhello\r\n\r\nworld\r\n")))

    assert parsed.parser_version == "text-v1"
    assert parsed.page_count == 0
    assert len(parsed.sections) == 1
    assert parsed.sections[0].content == "hello\n\nworld"
    assert parsed.sections[0].page_number is None
    assert parsed.sections[0].heading_path == ()


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"\xff\xfe\xfa", DocumentParseErrorCode.INVALID_TEXT_ENCODING),
        (b" \r\n\t ", DocumentParseErrorCode.NO_EXTRACTABLE_TEXT),
    ],
)
def test_text_parser_reports_stable_failures(
    content: bytes,
    expected_code: DocumentParseErrorCode,
) -> None:
    """编码错误和无有效文本必须产生 Worker 可持久化的稳定错误码。"""
    parser = TextParser()

    error = run_parse_error(parser.parse(BytesIO(content)))

    assert error.code == expected_code


def test_markdown_parser_preserves_heading_path_and_ignores_fenced_code() -> None:
    """Markdown 应维护标题层级，但代码围栏中的井号不能被误判为标题。"""
    markdown = """前言

# 产品
产品介绍。

## 安装
```python
# 这是代码注释，不是标题
print("ok")
```
安装步骤。

# FAQ
常见问题。
"""
    parser = MarkdownParser()

    parsed = asyncio.run(parser.parse(BytesIO(markdown.encode("utf-8"))))

    assert [section.heading_path for section in parsed.sections] == [
        (),
        ("产品",),
        ("产品", "安装"),
        ("FAQ",),
    ]
    install_section = parsed.sections[2]
    assert "# 这是代码注释，不是标题" in install_section.content
    assert "安装步骤。" in install_section.content
    assert parsed.parser_version == "markdown-v1"


def test_parser_registry_selects_by_case_insensitive_extension() -> None:
    """注册表应隐藏具体 Parser 选择，并支持大小写扩展名。"""
    registry = DocumentParserRegistry()

    assert isinstance(registry.get_parser("manual.PDF"), PdfParser)
    assert isinstance(registry.get_parser("notes.TXT"), TextParser)
    assert isinstance(registry.get_parser("README.Md"), MarkdownParser)
    assert isinstance(registry.get_parser("proposal.DOCX"), DocxParser)
    assert isinstance(registry.get_parser("students.XLSX"), XlsxParser)
    assert isinstance(registry.get_parser("scan.PNG"), ImageOcrParser)
    assert isinstance(registry.get_parser("receipt.JpEg"), ImageOcrParser)


def test_parser_registry_rejects_unsupported_extension() -> None:
    """尚未支持的旧版 Office 文件必须在解析前明确失败。"""
    registry = DocumentParserRegistry()

    with pytest.raises(UnsupportedDocumentTypeError):
        registry.get_parser("proposal.doc")


def test_pdf_parser_preserves_page_numbers_and_skips_empty_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF 提取结果应保留真实页码，空白页可跳过但不能改变后续页号。"""
    reader = SimpleNamespace(
        is_encrypted=False,
        pages=[
            FakePdfPage("第一页内容"),
            FakePdfPage("  "),
            FakePdfPage("第三页内容"),
        ],
    )
    monkeypatch.setattr(
        "app.modules.knowledge.processing.pdf_parser.PdfReader",
        lambda stream: reader,
    )
    parser = PdfParser()

    parsed = asyncio.run(parser.parse(BytesIO(b"fake pdf")))

    assert parsed.page_count == 3
    assert [section.page_number for section in parsed.sections] == [1, 3]
    assert [section.content for section in parsed.sections] == [
        "第一页内容",
        "第三页内容",
    ]
    assert parsed.parser_version == "pypdf-v1"


def test_pdf_parser_rejects_encrypted_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """第一版不接收加密 PDF，避免 Worker 等待密码或产生不完整索引。"""
    monkeypatch.setattr(
        "app.modules.knowledge.processing.pdf_parser.PdfReader",
        lambda stream: SimpleNamespace(is_encrypted=True, pages=[]),
    )

    error = run_parse_error(PdfParser().parse(BytesIO(b"encrypted")))

    assert error.code == DocumentParseErrorCode.PDF_ENCRYPTED


def test_pdf_parser_rejects_scanned_or_empty_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有页面均无可提取文字时应明确失败，第一版不会假装 OCR 成功。"""
    monkeypatch.setattr(
        "app.modules.knowledge.processing.pdf_parser.PdfReader",
        lambda stream: SimpleNamespace(
            is_encrypted=False,
            pages=[FakePdfPage(None), FakePdfPage(" \n")],
        ),
    )

    error = run_parse_error(PdfParser().parse(BytesIO(b"scanned")))

    assert error.code == DocumentParseErrorCode.NO_EXTRACTABLE_TEXT


def test_pdf_parser_maps_corrupted_file_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pypdf 内部异常不能直接泄露，Worker 只保存稳定解析错误码。"""

    def raise_corrupted(stream):
        """模拟 PdfReader 读取损坏文件失败。"""
        raise ValueError("broken xref")

    monkeypatch.setattr(
        "app.modules.knowledge.processing.pdf_parser.PdfReader",
        raise_corrupted,
    )

    error = run_parse_error(PdfParser().parse(BytesIO(b"broken")))

    assert error.code == DocumentParseErrorCode.PDF_INVALID
    assert "broken xref" not in error.message


def test_pdf_parser_extracts_text_from_real_pypdf_stream() -> None:
    """使用真实 pypdf 生成和解析一页文字，验证第三方接口没有只停留在 Mock。"""
    stream = build_real_text_pdf("Project meeting on Friday")

    parsed = asyncio.run(PdfParser().parse(stream))

    assert parsed.page_count == 1
    assert parsed.sections[0].page_number == 1
    assert "Project meeting on Friday" in parsed.sections[0].content


def test_chunker_keeps_pdf_pages_and_markdown_headings_isolated() -> None:
    """不同页码或标题路径不能被合并到同一个 Chunk。"""
    parsed = ParsedDocument(
        sections=(
            ParsedSection(content="第一页短内容", page_number=1),
            ParsedSection(content="第二页短内容", page_number=2),
            ParsedSection(content="安装说明", heading_path=("指南", "安装")),
        ),
        page_count=2,
        parser_version="test-v1",
    )
    chunker = TokenAwareChunker(
        token_counter=CharacterTokenCounter(),
        chunk_size_tokens=100,
        chunk_overlap_tokens=10,
    )

    chunks = chunker.chunk(parsed)

    assert len(chunks) == 3
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 1)
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)
    assert chunks[2].heading_path == "指南 > 安装"
    assert chunks[2].page_start is None


def test_chunker_enforces_budget_and_carries_bounded_overlap() -> None:
    """长文本必须按预算拆分，后一块应携带不超过配置量的前块结尾。"""
    parsed = ParsedDocument(
        sections=(
            ParsedSection(
                content="第一段内容12345\n\n第二段内容12345\n\n第三段内容12345"
            ),
        ),
        page_count=0,
        parser_version="text-v1",
    )
    chunker = TokenAwareChunker(
        token_counter=CharacterTokenCounter(),
        chunk_size_tokens=24,
        chunk_overlap_tokens=5,
    )

    chunks = chunker.chunk(parsed)

    assert len(chunks) >= 2
    assert all(chunk.token_count <= 24 for chunk in chunks)
    assert chunks[1].content.startswith(chunks[0].content[-5:])
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_chunker_splits_single_oversized_paragraph_without_empty_chunks() -> None:
    """单个超长段落也必须强制切分，且每个结果都非空并满足预算。"""
    parsed = ParsedDocument(
        sections=(ParsedSection(content="知" * 53, page_number=7),),
        page_count=7,
        parser_version="pypdf-v1",
    )
    chunker = TokenAwareChunker(
        token_counter=CharacterTokenCounter(),
        chunk_size_tokens=20,
        chunk_overlap_tokens=4,
    )

    chunks = chunker.chunk(parsed)

    assert len(chunks) >= 3
    assert all(chunk.content for chunk in chunks)
    assert all(chunk.token_count <= 20 for chunk in chunks)
    assert all(chunk.page_start == 7 and chunk.page_end == 7 for chunk in chunks)
    assert chunks[1].content.startswith(chunks[0].content[-4:])


def test_chunker_reuses_project_token_counter() -> None:
    """生产 Chunker 应可直接复用现有模型计数器，而不是退回字符长度。"""
    parsed = ParsedDocument(
        sections=(ParsedSection(content="企业知识库文档。" * 80),),
        page_count=0,
        parser_version="text-v1",
    )
    counter = TokenCounter(model_name="mock-embedding", encoder=False)
    chunker = TokenAwareChunker(
        token_counter=counter,
        chunk_size_tokens=60,
        chunk_overlap_tokens=10,
    )

    chunks = chunker.chunk(parsed)

    assert len(chunks) > 1
    assert all(chunk.token_count <= 60 for chunk in chunks)


def test_chunker_generates_stable_content_hash() -> None:
    """相同内容应产生相同 SHA-256，方便 Worker 做重复执行保护。"""
    parsed = ParsedDocument(
        sections=(ParsedSection(content="稳定内容"),),
        page_count=0,
        parser_version="text-v1",
    )
    chunker = TokenAwareChunker(
        token_counter=CharacterTokenCounter(),
        chunk_size_tokens=20,
        chunk_overlap_tokens=4,
    )

    first = chunker.chunk(parsed)
    second = chunker.chunk(parsed)

    assert first[0].content_hash == second[0].content_hash
    assert len(first[0].content_hash) == 64
    assert first[0].chunker_version == "token-paragraph-v1"


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, -1), (10, 10), (10, 11)],
)
def test_chunker_rejects_invalid_budget(chunk_size: int, overlap: int) -> None:
    """Chunk 大小必须为正，重叠必须非负且严格小于 Chunk 预算。"""
    with pytest.raises(ValueError):
        TokenAwareChunker(
            token_counter=CharacterTokenCounter(),
            chunk_size_tokens=chunk_size,
            chunk_overlap_tokens=overlap,
        )


class FakePdfPage:
    """模拟 pypdf PageObject 的文字提取接口。"""

    def __init__(self, text: str | None) -> None:
        """保存该页预期返回的文字。"""
        self.text = text

    def extract_text(self) -> str | None:
        """返回测试指定的页面文字。"""
        return self.text


def build_real_text_pdf(text: str) -> BytesIO:
    """使用标准 PDF 字体和内容流生成一页可提取文字的内存 PDF。"""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content_stream = DecodedStreamObject()
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream.set_data(
        f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii")
    )
    page[NameObject("/Contents")] = writer._add_object(content_stream)
    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output


def run_parse_error(awaitable) -> DocumentParseError:
    """运行解析操作并返回预期的稳定解析异常。"""

    async def scenario() -> DocumentParseError:
        """捕获 DocumentParseError，未失败时主动终止测试。"""
        try:
            await awaitable
        except DocumentParseError as exc:
            return exc
        raise AssertionError("解析操作必须失败")

    return asyncio.run(scenario())
