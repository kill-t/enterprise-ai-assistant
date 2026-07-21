from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRole,
)
from app.modules.conversation.entity.message import MessageEntity


class HistoryMapper:

    def to_chat_messages(
        self,
        messages: list[MessageEntity],
    ) -> list[ChatMessage]:

        return [
            ChatMessage(
                role=ChatRole(message.role.value),
                content=message.content,
            )
            for message in messages
        ]
