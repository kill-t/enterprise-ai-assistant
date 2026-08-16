"""定义跨 PDF、Word、Excel 和 OCR 共用的结构化来源位置协议。"""

import re
from pathlib import PurePath
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.common.enums import SourceType


class _ImmutableLocator(BaseModel):
    """禁止额外字段并冻结位置值，防止在检索链中被意外修改。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentSourceLocator(_ImmutableLocator):
    """表示没有页码或章节结构的整篇纯文本来源。"""

    kind: Literal["document"] = "document"


class PageSourceLocator(_ImmutableLocator):
    """表示 PDF 等固定版式文档中的连续页码范围。"""

    kind: Literal["page"] = "page"
    page_start: int = Field(gt=0)
    page_end: int = Field(gt=0)
    heading_path: tuple[str, ...] = ()

    @field_validator("heading_path")
    @classmethod
    def normalize_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """清理可选标题路径，并拒绝会产生空导航层级的值。"""
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("标题路径不能包含空层级")
        return normalized

    @model_validator(mode="after")
    def validate_page_order(self) -> "PageSourceLocator":
        """起始页不得位于结束页之后。"""
        if self.page_end < self.page_start:
            raise ValueError("结束页不能小于起始页")
        return self


class SectionSourceLocator(_ImmutableLocator):
    """表示 Markdown 或 Word 标题下的段落、列表或表格块。"""

    kind: Literal["section"] = "section"
    heading_path: tuple[str, ...] = Field(min_length=1)
    block_kind: Literal["paragraph", "list", "table"] | None = None
    block_index: int | None = Field(default=None, ge=0)

    @field_validator("heading_path")
    @classmethod
    def normalize_heading_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """去除标题两端空白并拒绝空标题。"""
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("标题路径不能包含空层级")
        return normalized

    @model_validator(mode="after")
    def validate_block_pair(self) -> "SectionSourceLocator":
        """块类型和块序号必须同时出现或同时缺失。"""
        if (self.block_kind is None) != (self.block_index is None):
            raise ValueError("block_kind 与 block_index 必须同时提供")
        return self


CELL_RANGE_PATTERN = re.compile(
    r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$"
)


class SheetRangeSourceLocator(_ImmutableLocator):
    """表示 Excel 工作表中包含表头语义的连续单元格区域。"""

    kind: Literal["sheet_range"] = "sheet_range"
    sheet_name: str = Field(min_length=1, max_length=255)
    cell_range: str = Field(pattern=CELL_RANGE_PATTERN.pattern)
    header_range: str | None = Field(
        default=None,
        pattern=CELL_RANGE_PATTERN.pattern,
    )

    @field_validator("sheet_name", mode="before")
    @classmethod
    def normalize_sheet_name(cls, value: object) -> object:
        """清理工作表名称两端空白，空字符串交给长度规则拒绝。"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("cell_range", "header_range", mode="before")
    @classmethod
    def normalize_cell_range(cls, value: object) -> object:
        """将 Excel 地址统一为大写，便于比较、展示和测试。"""
        return value.strip().upper() if isinstance(value, str) else value


class NormalizedBoundingBox(_ImmutableLocator):
    """使用 0 到 1 的归一化坐标描述 OCR 区域，避免绑定图片像素尺寸。"""

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_coordinate_order(self) -> "NormalizedBoundingBox":
        """右下角必须严格位于左上角之后，零面积区域没有引用意义。"""
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("OCR 区域坐标顺序无效")
        return self


class OcrRegionSourceLocator(_ImmutableLocator):
    """表示扫描 PDF 或图片中的 OCR 识别区域及其可审计置信度。"""

    kind: Literal["ocr_region"] = "ocr_region"
    page_number: int | None = Field(default=None, gt=0)
    bounding_box: NormalizedBoundingBox
    confidence: float = Field(ge=0.0, le=1.0)
    engine: str = Field(min_length=1, max_length=100)

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: object) -> object:
        """清理 OCR 引擎标识，避免只保存空白字符串。"""
        return value.strip() if isinstance(value, str) else value


SourceLocator: TypeAlias = Annotated[
    DocumentSourceLocator
    | PageSourceLocator
    | SectionSourceLocator
    | SheetRangeSourceLocator
    | OcrRegionSourceLocator,
    Field(discriminator="kind"),
]

SOURCE_LOCATOR_ADAPTER = TypeAdapter(SourceLocator)


def parse_source_locator(value: object) -> SourceLocator:
    """把数据库或 HTTP 中的未知对象校验为严格位置联合类型。"""
    return SOURCE_LOCATOR_ADAPTER.validate_python(value)


def serialize_source_locator(locator: SourceLocator) -> dict[str, object]:
    """生成可由 PostgreSQL JSONB 保存的普通 JSON 对象。"""
    return locator.model_dump(mode="json")


def build_legacy_source_locator(
    *,
    page_start: int | None,
    page_end: int | None,
    heading_path: tuple[str, ...],
) -> SourceLocator:
    """把当前 PDF、TXT、Markdown Parser 的旧位置字段映射到新契约。"""
    if page_start is not None:
        return PageSourceLocator(
            page_start=page_start,
            page_end=page_end or page_start,
            heading_path=heading_path,
        )
    if heading_path:
        return SectionSourceLocator(heading_path=heading_path)
    return DocumentSourceLocator()


def infer_source_type(filename: str) -> SourceType:
    """根据已经通过上传白名单的文件扩展名生成稳定来源类型。"""
    extension = PurePath(filename).suffix.lower()
    source_types = {
        ".pdf": SourceType.PDF,
        ".txt": SourceType.TEXT,
        ".md": SourceType.MARKDOWN,
        ".markdown": SourceType.MARKDOWN,
        ".docx": SourceType.DOCX,
        ".xlsx": SourceType.XLSX,
        ".png": SourceType.IMAGE,
        ".jpg": SourceType.IMAGE,
        ".jpeg": SourceType.IMAGE,
    }
    try:
        return source_types[extension]
    except KeyError as exc:
        raise ValueError(f"无法识别文档来源类型：{extension or '无扩展名'}") from exc
