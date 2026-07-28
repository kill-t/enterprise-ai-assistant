from app.infrastructure.llm.schemas import ChatMessage
from app.infrastructure.llm.token_counter import TokenCounter


class ContextManager:
    """在上下文预算内选择尽可能新的历史消息。"""

    def __init__(
        self,
        max_tokens: int = 4000,
    ):
        """配置历史消息最大预算并初始化计数器。"""
        self.max_tokens = max_tokens
        self.token_counter = TokenCounter()

    def build_history(
        self,
        history: list[ChatMessage],
    ) -> list[ChatMessage]:
        """从新到旧选择消息，超出预算后停止并恢复时间顺序。"""

        selected_messages: list[ChatMessage] = []
        current_tokens = 0

        # 逆序遍历可以优先保留与当前问题最接近的消息。
        for message in reversed(history):

            message_tokens = self.token_counter.count(message.content)

            if current_tokens + message_tokens > self.max_tokens:
                break

            selected_messages.append(message)
            current_tokens += message_tokens

        selected_messages.reverse()

        return selected_messages
