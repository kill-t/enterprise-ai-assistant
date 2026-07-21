from app.infrastructure.llm.base import BaseChatProvider

from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
)


class MockProvider(BaseChatProvider):

    MODEL_NAME = "mock"

    def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        return ChatResponse(
            content="Hello, I'm Enterprise AI Assistant.",
            model=self.MODEL_NAME,
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        )
