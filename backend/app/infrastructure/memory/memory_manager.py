from app.infrastructure.memory.base import BaseMemory
from app.infrastructure.memory.schemas import MemoryMessage


class MemoryManager:
    """向业务层提供与具体存储实现无关的短期记忆操作。"""

    def __init__(
        self,
        short_term_memory: BaseMemory,
    ):
        """注入当前使用的短期记忆存储。"""
        self.short_term_memory = short_term_memory

    async def save_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:
        """保存一条会话消息到短期记忆。"""

        await self.short_term_memory.add_message(
            conversation_id,
            message,
        )

    async def get_context(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        """返回可用于构建模型上下文的短期消息。"""

        return await self.short_term_memory.get_messages(conversation_id)

    async def delete_context(self, conversation_id: str) -> None:
        """删除指定会话的全部短期缓存。"""
        await self.short_term_memory.delete_messages(conversation_id)
