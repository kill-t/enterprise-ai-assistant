import re
from typing import BinaryIO

from app.modules.knowledge.processing.schemas import ParsedDocument, ParsedSection
from app.modules.knowledge.processing.text_parser import TextParser


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")


class MarkdownParser(TextParser):
    """按 Markdown 标题层级切出来源段，并保留代码围栏原文。"""

    parser_version = "markdown-v1"

    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """解码 Markdown，并构建每段对应的标题路径。"""
        text = await self._read_utf8_text(stream)
        sections: list[ParsedSection] = []
        heading_stack: list[str] = []
        current_path: tuple[str, ...] = ()
        current_lines: list[str] = []
        fence_marker: str | None = None

        def flush_section() -> None:
            """把当前标题下的非空原文保存成一个来源段。"""
            content = "\n".join(current_lines).strip()
            if content:
                sections.append(
                    ParsedSection(
                        content=content,
                        heading_path=current_path,
                    )
                )
            current_lines.clear()

        for line in text.split("\n"):
            fence_match = FENCE_PATTERN.match(line)
            if fence_marker is not None:
                current_lines.append(line)
                if line.lstrip().startswith(fence_marker):
                    fence_marker = None
                continue

            if fence_match is not None:
                fence_marker = fence_match.group(1)
                current_lines.append(line)
                continue

            heading_match = HEADING_PATTERN.match(line)
            if heading_match is None:
                current_lines.append(line)
                continue

            flush_section()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip().rstrip("#").rstrip()
            # 标题跳级时保留已有父级；同级或回退时替换对应层级。
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(title)
            current_path = tuple(heading_stack)
            # 标题本身也属于原文，让后续 Embedding 能检索到标题关键词。
            current_lines.append(line)

        flush_section()
        return ParsedDocument(
            sections=tuple(sections),
            page_count=0,
            parser_version=self.parser_version,
        )
