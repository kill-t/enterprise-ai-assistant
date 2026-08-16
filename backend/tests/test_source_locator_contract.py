from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.common.enums import SourceType
from app.modules.conversation.repository.message_citation_repository import (
    MessageCitationRepository,
)
from app.modules.knowledge.processing.chunker import TokenAwareChunker
from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchHit,
)
from app.modules.knowledge.source_locator import (
    DocumentSourceLocator,
    OcrRegionSourceLocator,
    PageSourceLocator,
    SectionSourceLocator,
    SheetRangeSourceLocator,
    infer_source_type,
    parse_source_locator,
)


class _CharacterTokenCounter:
    """用字符数提供稳定 Token 结果，使测试只关注来源位置而不是 tokenizer。"""

    def count(self, text: str) -> int:
        """返回非空文本的字符数。"""
        return len(text)


def test_source_locator_discriminated_contract_accepts_each_supported_shape() -> None:
    """位置契约必须区分页码、章节、表格区域和 OCR 区域。"""
    page = parse_source_locator(
        {
            "kind": "page",
            "page_start": 2,
            "page_end": 3,
            "heading_path": ["申请条件"],
        }
    )
    section = parse_source_locator(
        {
            "kind": "section",
            "heading_path": ["资助政策", "申请条件"],
            "block_kind": "table",
            "block_index": 1,
        }
    )
    sheet = parse_source_locator(
        {
            "kind": "sheet_range",
            "sheet_name": "助学金标准",
            "cell_range": "A2:F12",
            "header_range": "A1:F1",
        }
    )
    ocr = parse_source_locator(
        {
            "kind": "ocr_region",
            "page_number": 4,
            "bounding_box": {
                "x_min": 0.1,
                "y_min": 0.2,
                "x_max": 0.8,
                "y_max": 0.7,
            },
            "confidence": 0.91,
            "engine": "paddleocr",
        }
    )
    document = parse_source_locator({"kind": "document"})

    assert isinstance(page, PageSourceLocator)
    assert isinstance(section, SectionSourceLocator)
    assert isinstance(sheet, SheetRangeSourceLocator)
    assert isinstance(ocr, OcrRegionSourceLocator)
    assert isinstance(document, DocumentSourceLocator)


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "page", "page_start": 3, "page_end": 2},
        {"kind": "section", "heading_path": []},
        {"kind": "sheet_range", "sheet_name": "Sheet1", "cell_range": "错误范围"},
        {
            "kind": "ocr_region",
            "bounding_box": {
                "x_min": 0.8,
                "y_min": 0.2,
                "x_max": 0.1,
                "y_max": 0.7,
            },
            "confidence": 1.1,
            "engine": "paddleocr",
        },
    ],
)
def test_source_locator_rejects_contradictory_or_out_of_range_values(
    payload: dict[str, object],
) -> None:
    """应用层必须在数据写入和响应前拒绝互相矛盾的位置。"""
    with pytest.raises(ValidationError):
        parse_source_locator(payload)


def test_source_type_is_derived_from_filename_without_trusting_letter_case() -> None:
    """入库时从受支持扩展名生成稳定来源类型，为后续 Parser Adapter 复用。"""
    assert infer_source_type("政策.PDF") == SourceType.PDF
    assert infer_source_type("会议纪要.MarkDown") == SourceType.MARKDOWN
    assert infer_source_type("名单.XLSX") == SourceType.XLSX
    assert infer_source_type("扫描件.JpEg") == SourceType.IMAGE

    with pytest.raises(ValueError, match="无法识别文档来源类型"):
        infer_source_type("未知文件.bin")


def test_chunker_builds_locator_without_discarding_legacy_page_and_heading_fields() -> None:
    """当前 PDF 和 Markdown 仍保留旧字段，同时产生新的结构化位置。"""
    chunker = TokenAwareChunker(
        token_counter=_CharacterTokenCounter(),
        chunk_size_tokens=100,
        chunk_overlap_tokens=10,
    )
    page_chunk = chunker.chunk(
        ParsedDocument(
            sections=(ParsedSection(content="PDF 正文", page_number=2),),
            page_count=1,
            parser_version="test",
        )
    )[0]
    section_chunk = chunker.chunk(
        ParsedDocument(
            sections=(
                ParsedSection(
                    content="Markdown 正文",
                    heading_path=("资助政策", "申请条件"),
                ),
            ),
            page_count=0,
            parser_version="test",
        )
    )[0]

    assert page_chunk.page_start == 2
    assert page_chunk.source_locator == PageSourceLocator(
        page_start=2,
        page_end=2,
    )
    assert section_chunk.heading_path == "资助政策 > 申请条件"
    assert section_chunk.source_locator == SectionSourceLocator(
        heading_path=("资助政策", "申请条件"),
    )


def test_chunk_and_citation_entities_require_jsonb_locator_snapshots() -> None:
    """Chunk 保存当前位置，引用保存不可变快照，两列都不能依赖可空旧字段。"""
    from app.modules.conversation.entity.message_citation import (
        MessageCitationEntity,
    )
    from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity

    chunk_table = DocumentChunkEntity.__table__
    citation_table = MessageCitationEntity.__table__

    assert chunk_table.c.source_type.nullable is False
    assert isinstance(chunk_table.c.source_locator.type, JSONB)
    assert chunk_table.c.source_locator.nullable is False
    assert citation_table.c.source_type_snapshot.nullable is False
    assert isinstance(citation_table.c.source_locator_snapshot.type, JSONB)
    assert citation_table.c.source_locator_snapshot.nullable is False

    chunk_constraints = {
        item.name for item in chunk_table.constraints if isinstance(item, CheckConstraint)
    }
    citation_constraints = {
        item.name
        for item in citation_table.constraints
        if isinstance(item, CheckConstraint)
    }
    assert "ck_document_chunks_source_locator_object" in chunk_constraints
    assert "ck_message_citations_source_locator_object" in citation_constraints


def test_citation_repository_freezes_source_type_and_locator() -> None:
    """引用快照不得依赖以后可能删除或重新入库的 Chunk。"""
    # 聚合导入保证 SQLAlchemy 在首次创建实体前已经注册全部关系目标。
    from app.modules.knowledge import models

    assert models.DocumentIngestionJobEntity.__tablename__ == "document_ingestion_jobs"
    locator = SheetRangeSourceLocator(
        sheet_name="资助标准",
        cell_range="A2:D8",
        header_range="A1:D1",
    )
    source = MultiKnowledgeSearchHit(
        knowledge_base_id=uuid4(),
        knowledge_base_name="学生工作资料",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="助学金标准.xlsx",
        chunk_index=0,
        content="等级 金额",
        heading_path=None,
        page_start=None,
        page_end=None,
        token_count=8,
        score=0.9,
        source_type=SourceType.XLSX,
        source_locator=locator,
    )

    entity = MessageCitationRepository._to_entity(
        message_id=uuid4(),
        source=source,
        source_order=1,
    )

    assert entity.source_type_snapshot == SourceType.XLSX
    assert entity.source_locator_snapshot == locator.model_dump(mode="json")


def test_source_locator_migration_backfills_before_enforcing_not_null() -> None:
    """现有 PDF、TXT、Markdown 数据必须可无损升级，并允许安全降级。"""
    migration = Path(
        "migrations/versions/d4e8b6c1a209_add_structured_source_locators.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "a1c5e9d3f702"' in migration
    assert 'postgresql.JSONB(astext_type=sa.Text())' in migration
    assert "UPDATE document_chunks" in migration
    assert "UPDATE message_citations" in migration
    assert migration.index("UPDATE document_chunks") < migration.index(
        'alter_column("document_chunks", "source_type"'
    )
    assert 'op.drop_column("message_citations", "source_locator_snapshot")' in migration
    assert 'op.drop_column("document_chunks", "source_type")' in migration
