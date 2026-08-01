from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.core.loggers import llm_logger
from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamCompletedEvent,
    ChatStreamDeltaEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
)


class BaseChatProvider(ABC):
    """所有聊天模型 Provider 必须实现的统一接口。"""

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """异步发送标准聊天请求，并返回与厂商实现无关的响应结构。"""
        raise NotImplementedError

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """把非流式实现兼容为单片内部流，原生流 Provider 可以覆盖此方法。

        默认实现让尚未接入厂商流协议的 Provider 仍满足统一接口，但它不会
        提前返回内容，只会在普通 ``chat`` 完成后产生一个 delta 和 completed。
        """
        try:
            response = await self.chat(request)
        except Exception as exc:
            # asyncio.CancelledError 不属于 Exception，后续取消请求仍能向外传播。
            llm_logger.warning(
                "默认模型流式适配失败 provider=%s reason=%s",
                type(self).__name__,
                type(exc).__name__,
                exc_info=True,
            )
            yield ChatStreamErrorEvent(
                error_code="LLM_STREAM_ERROR",
                error_message="模型流式调用失败",
                model=request.model,
            )
            return

        if response.content:
            yield ChatStreamDeltaEvent(delta=response.content)
        yield ChatStreamCompletedEvent(response=response)
