from abc import ABC, abstractmethod
from typing import BinaryIO

from app.modules.knowledge.processing.schemas import ParsedDocument


class BaseDocumentParser(ABC):
    """定义各种文件解析器必须实现的异步统一契约。"""

    @abstractmethod
    async def parse(self, stream: BinaryIO) -> ParsedDocument:
        """从可定位的二进制流提取有序文本和来源元数据。"""
