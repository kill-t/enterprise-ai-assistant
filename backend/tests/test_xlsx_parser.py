import asyncio
from datetime import date
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from app.modules.knowledge.processing.errors import (
    DocumentParseError,
    DocumentParseErrorCode,
)
from app.modules.knowledge.processing.xlsx_parser import XlsxParser
from app.modules.knowledge.source_locator import SheetRangeSourceLocator


def test_xlsx_parser_preserves_typed_values_formula_and_sheet_range() -> None:
    """可见工作表应保留数字、日期、布尔值和未缓存公式，并生成精确范围。"""
    content = build_xlsx(lambda workbook: fill_policy_sheet(workbook.active))

    parsed = asyncio.run(XlsxParser().parse(BytesIO(content)))

    assert parsed.parser_version == "xlsx-openpyxl-v1"
    assert parsed.page_count == 0
    assert len(parsed.sections) == 1
    section = parsed.sections[0]
    assert section.heading_path == ("资助标准",)
    assert section.content == (
        "工作表：资助标准\n\n"
        "| 姓名 | 金额 | 是否通过 | 申请日期 | 双倍金额 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 张三 | 5000 | TRUE | 2026-08-22 | =B2*2 |"
    )
    assert section.source_locator == SheetRangeSourceLocator(
        sheet_name="资助标准",
        cell_range="A2:E2",
        header_range="A1:E1",
    )


def test_xlsx_parser_repeats_header_and_splits_data_by_row_budget() -> None:
    """长表格应按完整行分块，每块重复表头且 Locator 只覆盖真实数据行。"""
    content = build_xlsx(lambda workbook: fill_student_rows(workbook.active, 5))

    parsed = asyncio.run(XlsxParser(max_rows_per_block=2).parse(BytesIO(content)))

    assert [section.source_locator for section in parsed.sections] == [
        SheetRangeSourceLocator(
            sheet_name="学生名单",
            cell_range="A2:B3",
            header_range="A1:B1",
        ),
        SheetRangeSourceLocator(
            sheet_name="学生名单",
            cell_range="A4:B5",
            header_range="A1:B1",
        ),
        SheetRangeSourceLocator(
            sheet_name="学生名单",
            cell_range="A6:B6",
            header_range="A1:B1",
        ),
    ]
    assert all("| 学号 | 姓名 |" in section.content for section in parsed.sections)
    assert "| 1001 | 学生1 |" in parsed.sections[0].content
    assert "| 1005 | 学生5 |" in parsed.sections[2].content


def test_xlsx_parser_separates_blank_row_regions_and_side_by_side_tables() -> None:
    """空行和空列应把多个逻辑表格拆开，避免引用跨越无关区域。"""

    def builder(workbook: Workbook) -> None:
        sheet = workbook.active
        sheet.title = "多区域"
        sheet.append(["项目", "金额", None, "部门", "负责人"])
        sheet.append(["奖学金", 5000, None, "研发", "李老师"])
        sheet.append([])
        sheet.append(["年份", "人数"])
        sheet.append([2025, 30])

    parsed = asyncio.run(XlsxParser().parse(BytesIO(build_xlsx(builder))))

    assert [section.source_locator for section in parsed.sections] == [
        SheetRangeSourceLocator(
            sheet_name="多区域", cell_range="A2:B2", header_range="A1:B1"
        ),
        SheetRangeSourceLocator(
            sheet_name="多区域", cell_range="D2:E2", header_range="D1:E1"
        ),
        SheetRangeSourceLocator(
            sheet_name="多区域", cell_range="A5:B5", header_range="A4:B4"
        ),
    ]


def test_xlsx_parser_skips_hidden_sheet_and_fails_when_visible_sheets_are_empty() -> (
    None
):
    """隐藏辅助表不应意外进入知识库，所有可见表为空时明确失败。"""

    def builder(workbook: Workbook) -> None:
        visible = workbook.active
        visible.title = "说明"
        hidden = workbook.create_sheet("内部计算")
        hidden.sheet_state = "hidden"
        hidden.append(["不应入库"])

    content = build_xlsx(builder)

    error = run_parse_error(XlsxParser().parse(BytesIO(content)))

    assert error.code == DocumentParseErrorCode.NO_EXTRACTABLE_TEXT


def test_xlsx_parser_rejects_invalid_external_link_and_row_limit() -> None:
    """损坏包、外链部件和超出行上限的工作簿必须产生稳定失败。"""
    invalid_error = run_parse_error(XlsxParser().parse(BytesIO(b"not-a-zip")))
    assert invalid_error.code == DocumentParseErrorCode.XLSX_INVALID

    content = BytesIO(
        build_xlsx(lambda workbook: fill_student_rows(workbook.active, 1))
    )
    with ZipFile(content, mode="a", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/externalLinks/externalLink1.xml", b"external")
    external_error = run_parse_error(XlsxParser().parse(BytesIO(content.getvalue())))
    assert external_error.code == DocumentParseErrorCode.OFFICE_ARCHIVE_UNSAFE

    oversized = build_xlsx(lambda workbook: fill_student_rows(workbook.active, 3))
    limit_error = run_parse_error(
        XlsxParser(max_rows_per_sheet=3).parse(BytesIO(oversized))
    )
    assert limit_error.code == DocumentParseErrorCode.XLSX_LIMIT_EXCEEDED


def fill_policy_sheet(sheet) -> None:
    """填充包含多种 Excel 值类型和公式的资助政策表。"""
    sheet.title = "资助标准"
    sheet.append(["姓名", "金额", "是否通过", "申请日期", "双倍金额"])
    sheet.append(["张三", 5000, True, date(2026, 8, 22), "=B2*2"])


def fill_student_rows(sheet, count: int) -> None:
    """填充指定数量的学生数据行。"""
    sheet.title = "学生名单"
    sheet.append(["学号", "姓名"])
    for index in range(1, count + 1):
        sheet.append([1000 + index, f"学生{index}"])


def build_xlsx(builder) -> bytes:
    """在内存中生成真实 XLSX，避免测试依赖外部样本文件。"""
    workbook = Workbook()
    builder(workbook)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def run_parse_error(awaitable) -> DocumentParseError:
    """运行异步解析并返回预期错误。"""

    async def scenario() -> DocumentParseError:
        """捕获解析错误；没有失败时让测试明确终止。"""
        try:
            await awaitable
        except DocumentParseError as exc:
            return exc
        raise AssertionError("解析必须失败")

    return asyncio.run(scenario())
