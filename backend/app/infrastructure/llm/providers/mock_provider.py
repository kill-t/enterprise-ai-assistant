import asyncio
import json
from collections.abc import AsyncIterator

from app.core.loggers import llm_logger
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
    ChatResponseFormat,
    ChatStreamCompletedEvent,
    ChatStreamDeltaEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
)


class MockProvider(BaseChatProvider):
    """返回固定响应的测试 Provider，不访问外部模型服务。"""

    MODEL_NAME = "mock"
    STREAM_CHUNKS = ("Hello, ", "I'm Enterprise ", "AI Assistant", ".")

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """异步生成可预测的模拟聊天响应与 token 用量。"""

        if (
            request.response_format == ChatResponseFormat.JSON_OBJECT
            and request.metadata.get("operation") == "conversation_summary"
        ):
            # 本地开发不访问真实模型，但仍返回能通过同一 Pydantic 校验的摘要结构。
            content = json.dumps(
                {
                    "user_goal_and_context": "本地模拟会话目标",
                    "confirmed_facts_and_decisions": "暂无已确认事实",
                    "completed_items": "暂无已完成事项",
                    "open_questions_and_todos": "暂无待办",
                    "useful_preferences": "暂无明确偏好",
                },
                ensure_ascii=False,
            )
            return ChatResponse(
                content=content,
                model=self.MODEL_NAME,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            )

        return ChatResponse(
            content="Hello, I'm Enterprise AI Assistant.",
            model=self.MODEL_NAME,
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        )

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """把固定响应分成可预测片段，并支持显式模拟中途异常。"""
        try:
            response = await self.chat(request)
            chunks = (
                self.STREAM_CHUNKS
                if response.content == "".join(self.STREAM_CHUNKS)
                else (response.content,)
            )
            for index, chunk in enumerate(chunks):
                # 测试开关只存在于 Mock Provider，用来稳定复现“已输出部分文本后失败”。
                if (
                    request.metadata.get("simulate_stream_error") == "true"
                    and index == 1
                ):
                    raise ConnectionError("模拟模型流连接中断")
                if chunk:
                    yield ChatStreamDeltaEvent(delta=chunk)
                # 主动交还事件循环，模拟真实网络流等待下一个数据块。
                await asyncio.sleep(0)
        except Exception as exc:
            llm_logger.warning(
                "Mock 模型流式生成失败 reason=%s",
                type(exc).__name__,
                exc_info=True,
            )
            yield ChatStreamErrorEvent(
                error_code="LLM_STREAM_ERROR",
                error_message="模型流式调用失败",
                model=self.MODEL_NAME,
            )
            return

        yield ChatStreamCompletedEvent(response=response)
