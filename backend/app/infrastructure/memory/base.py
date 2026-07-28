from abc import ABC, abstractmethod

from app.infrastructure.memory.schemas import MemoryMessage


class BaseMemory(ABC):
    """短期记忆存储实现必须遵循的异步接口。"""

    @abstractmethod
    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ):
        """向指定会话追加一条短期记忆消息。"""
        pass

    @abstractmethod
    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        """读取指定会话当前保留的短期记忆消息。"""
        pass
