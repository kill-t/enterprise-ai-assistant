from app.infrastructure.llm.schemas import ChatMessage
from app.infrastructure.llm.token_counter import TokenCounter


class ContextManager:

    def __init__(
        self,
        max_tokens: int = 4000,
    ):
        self.max_tokens = max_tokens
        self.token_counter = TokenCounter()

    def build_history(
        self,
        history: list[ChatMessage],
    ) -> list[ChatMessage]:

        selected_messages: list[ChatMessage] = []
        current_tokens = 0

        for message in reversed(history):

            message_tokens = self.token_counter.count(message.content)

            if current_tokens + message_tokens > self.max_tokens:
                break

            selected_messages.append(message)
            current_tokens += message_tokens

        selected_messages.reverse()

        return selected_messages
