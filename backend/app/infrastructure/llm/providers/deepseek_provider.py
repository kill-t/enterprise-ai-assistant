import time
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.core.config import settings
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


class DeepSeekProvider(BaseChatProvider):
    """通过 OpenAI 兼容接口调用 DeepSeek 聊天模型。"""

    PROVIDER_NAME = "deepseek"

    def __init__(self, *, thinking_enabled: bool | None = None):
        """根据配置创建客户端，并允许内部任务显式控制 DeepSeek 思考模式。"""

        self.client = AsyncOpenAI(
            api_key=settings.deepseek.api_key,
            base_url=settings.deepseek.base_url,
        )
        self.thinking_enabled = thinking_enabled

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """异步执行非流式模型调用，并记录 token 与调用耗时。"""
        start = time.perf_counter()

        request_options = {
            # 内部任务可显式选择模型；普通聊天未传时仍使用全局配置。
            "model": request.model or settings.deepseek.model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": settings.deepseek.max_output_tokens,
            "stream": False,
        }
        if request.response_format == ChatResponseFormat.JSON_OBJECT:
            # DeepSeek JSON Output 需要同时传递 response_format，并在 Prompt 中明确要求 JSON。
            request_options["response_format"] = {"type": "json_object"}
        self._apply_thinking_option(request_options)

        response = await self.client.chat.completions.create(**request_options)

        elapsed = time.perf_counter() - start
        llm_logger.info(
            (
                "provider=%s "
                "model=%s "
                "prompt_tokens=%s "
                "completion_tokens=%s "
                "total_tokens=%s "
                "latency=%.3fs"
            ),
            self.PROVIDER_NAME,
            response.model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
            elapsed,
        )

        return ChatResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """消费 DeepSeek 的异步 Chunk 流，并转换成厂商无关的内部事件。"""
        start = time.perf_counter()
        content_parts: list[str] = []
        model = request.model or settings.deepseek.model
        finish_reason = "stop"
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        request_options = {
            "model": model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": settings.deepseek.max_output_tokens,
            "stream": True,
            # DeepSeek 会在正文结束后追加一个 choices 为空、usage 非空的数据块。
            "stream_options": {"include_usage": True},
        }
        if request.response_format == ChatResponseFormat.JSON_OBJECT:
            request_options["response_format"] = {"type": "json_object"}
        self._apply_thinking_option(request_options)

        try:
            sdk_stream = await self.client.chat.completions.create(**request_options)
            async for chunk in sdk_stream:
                model = chunk.model or model

                # usage 通常只出现在最后一个 choices 为空的数据块中。
                if chunk.usage is not None:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                    total_tokens = chunk.usage.total_tokens

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content
                if delta:
                    content_parts.append(delta)
                    yield ChatStreamDeltaEvent(delta=delta)
        except Exception as exc:
            llm_logger.warning(
                "DeepSeek 模型流式调用失败 provider=%s model=%s reason=%s latency=%.3fs",
                self.PROVIDER_NAME,
                model,
                type(exc).__name__,
                time.perf_counter() - start,
                exc_info=True,
            )
            yield ChatStreamErrorEvent(
                error_code="LLM_STREAM_ERROR",
                error_message="模型流式调用失败",
                model=model,
            )
            return

        response = ChatResponse(
            content="".join(content_parts),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
        )
        llm_logger.info(
            (
                "DeepSeek 模型流式调用完成 provider=%s model=%s "
                "prompt_tokens=%s completion_tokens=%s total_tokens=%s "
                "latency=%.3fs"
            ),
            self.PROVIDER_NAME,
            response.model,
            response.prompt_tokens,
            response.completion_tokens,
            response.total_tokens,
            time.perf_counter() - start,
        )
        yield ChatStreamCompletedEvent(response=response)

    def _apply_thinking_option(self, request_options: dict) -> None:
        """仅在调用方显式选择时发送厂商参数，保持现有聊天默认行为不变。"""
        thinking_enabled = getattr(self, "thinking_enabled", None)
        if thinking_enabled is None:
            return
        request_options["extra_body"] = {
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled",
            }
        }

    def _build_messages(
        self,
        request: ChatRequest,
    ) -> list[dict]:
        """将内部消息模型转换为 OpenAI 兼容字典。"""
        return [
            {
                "role": message.role.value,
                "content": message.content,
            }
            for message in request.messages
        ]
