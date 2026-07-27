from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRole,
)

from app.infrastructure.memory.schemas import MemoryMessage


class HistoryMapper:

    def to_chat_messages(
        self,
        messages: list[MemoryMessage],
    ) -> list[ChatMessage]:

        return [
            ChatMessage(
                role=ChatRole(message.role),
                content=message.content,
            )
            for message in messages
        ]
