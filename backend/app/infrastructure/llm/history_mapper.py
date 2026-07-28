from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRole,
)

from app.infrastructure.memory.schemas import MemoryMessage


class HistoryMapper:
    """将缓存消息转换为 LLM 层使用的标准聊天消息。"""

    def to_chat_messages(
        self,
        messages: list[MemoryMessage],
    ) -> list[ChatMessage]:
        """校验角色并复制消息内容，隔离 Memory 与 LLM 数据结构。"""

        return [
            ChatMessage(
                role=ChatRole(message.role),
                content=message.content,
            )
            for message in messages
        ]
