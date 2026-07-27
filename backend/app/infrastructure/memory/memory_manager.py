from app.infrastructure.memory.base import BaseMemory
from app.infrastructure.memory.schemas import MemoryMessage


class MemoryManager:

    def __init__(
        self,
        short_term_memory: BaseMemory,
    ):
        self.short_term_memory = short_term_memory

    async def save_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:

        await self.short_term_memory.add_message(
            conversation_id,
            message,
        )

    async def get_context(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:

        return await self.short_term_memory.get_messages(conversation_id)
