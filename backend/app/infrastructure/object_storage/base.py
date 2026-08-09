from abc import ABC, abstractmethod
from typing import BinaryIO


class BaseObjectStorage(ABC):
    """定义业务层可使用的异步对象存储能力。"""

    @abstractmethod
    async def ensure_bucket(self) -> None:
        """确保配置的私有存储桶存在。"""

    @abstractmethod
    async def put_object(
        self,
        object_key: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> None:
        """把指定长度的文件流保存到对象键。"""

    @abstractmethod
    async def get_object(self, object_key: str, max_bytes: int) -> bytes:
        """下载不超过指定字节数的完整对象。"""

    @abstractmethod
    async def delete_object(self, object_key: str) -> None:
        """删除对象，用于数据库失败后的补偿清理。"""
