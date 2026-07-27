from abc import ABC, abstractmethod

from app.infrastructure.memory.schemas import MemoryMessage


class BaseMemory(ABC):

    @abstractmethod
    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ):
        pass

    @abstractmethod
    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        pass
