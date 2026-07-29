from abc import ABC, abstractmethod

from app.infrastructure.llm.schemas import ChatRequest, ChatResponse


class BaseChatProvider(ABC):
    """所有聊天模型 Provider 必须实现的统一接口。"""

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """异步发送标准聊天请求，并返回与厂商实现无关的响应结构。"""
        raise NotImplementedError
