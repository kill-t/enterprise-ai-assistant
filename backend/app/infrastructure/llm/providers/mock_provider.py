from app.infrastructure.llm.base import BaseChatProvider

from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
)


class MockProvider(BaseChatProvider):
    """返回固定响应的测试 Provider，不访问外部模型服务。"""

    MODEL_NAME = "mock"

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """异步生成可预测的模拟聊天响应与 token 用量。"""

        return ChatResponse(
            content="Hello, I'm Enterprise AI Assistant.",
            model=self.MODEL_NAME,
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        )
