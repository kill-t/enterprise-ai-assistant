import asyncio
import inspect
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm import provider_factory as provider_factory_module
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.infrastructure.llm.providers.deepseek_provider import DeepSeekProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatRole,
    ChatStreamCompletedEvent,
    ChatStreamDeltaEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
    ChatStreamEventType,
)


class OneShotProvider(BaseChatProvider):
    """只实现普通 chat，用来验证基类提供的单片兼容流。"""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """返回固定完整响应，不实现厂商原生流。"""
        return ChatResponse(content="完整回答", model="one-shot")


async def collect_stream(
    provider: BaseChatProvider,
    request: ChatRequest,
) -> list[ChatStreamEvent]:
    """消费 Provider 的全部内部流事件，便于断言事件顺序和最终结果。"""
    return [event async for event in provider.stream_chat(request)]


def build_request(**metadata: str) -> ChatRequest:
    """创建流式契约测试使用的最小聊天请求。"""
    return ChatRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="整理会议纪要")],
        metadata=metadata,
    )


def test_base_provider_stream_chat_is_an_async_generator_contract() -> None:
    """流式接口必须返回异步生成器，调用方才能使用 async for 逐段消费。"""
    assert inspect.isasyncgenfunction(BaseChatProvider.stream_chat)


def test_delta_event_rejects_empty_text() -> None:
    """空 SDK 数据块不能伪装成正文增量，Provider 应直接跳过。"""
    with pytest.raises(ValidationError):
        ChatStreamDeltaEvent(delta="")


def test_base_provider_adapts_non_stream_chat_to_one_delta_and_completed() -> None:
    """未覆盖原生流的 Provider 仍应通过基类得到统一事件序列。"""
    events = asyncio.run(collect_stream(OneShotProvider(), build_request()))

    assert isinstance(events[0], ChatStreamDeltaEvent)
    assert events[0].delta == "完整回答"
    assert isinstance(events[1], ChatStreamCompletedEvent)
    assert events[1].response.content == "完整回答"


def test_mock_stream_preserves_chunk_order_and_completed_response() -> None:
    """Mock 流应按固定顺序输出片段，并在完成事件中保存完整响应。"""
    events = asyncio.run(collect_stream(MockProvider(), build_request()))

    assert [event.type for event in events] == [
        ChatStreamEventType.DELTA,
        ChatStreamEventType.DELTA,
        ChatStreamEventType.DELTA,
        ChatStreamEventType.DELTA,
        ChatStreamEventType.COMPLETED,
    ]
    deltas = [
        event.delta for event in events if isinstance(event, ChatStreamDeltaEvent)
    ]
    completed = events[-1]

    assert deltas == ["Hello, ", "I'm Enterprise ", "AI Assistant", "."]
    assert isinstance(completed, ChatStreamCompletedEvent)
    assert completed.response.content == "".join(deltas)
    assert completed.response.total_tokens == 18


def test_mock_stream_converts_provider_exception_to_error_event() -> None:
    """中途异常必须以安全 error 事件结束，不能继续产生 completed。"""
    events = asyncio.run(
        collect_stream(
            MockProvider(),
            build_request(simulate_stream_error="true"),
        )
    )

    assert isinstance(events[0], ChatStreamDeltaEvent)
    assert isinstance(events[-1], ChatStreamErrorEvent)
    assert events[-1].type == ChatStreamEventType.ERROR
    assert events[-1].error_code == "LLM_STREAM_ERROR"
    assert not any(isinstance(event, ChatStreamCompletedEvent) for event in events)


def test_deepseek_stream_adapts_sdk_chunks_usage_and_finish_reason() -> None:
    """DeepSeek 适配器应忽略空块、拼接正文，并读取最后的 usage 块。"""

    async def sdk_stream() -> AsyncIterator[SimpleNamespace]:
        """模拟 OpenAI-compatible SDK 返回的异步 Chunk 序列。"""
        yield SimpleNamespace(
            model="deepseek-chat",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="会议"),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        yield SimpleNamespace(
            model="deepseek-chat",
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="完成"),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        yield SimpleNamespace(
            model="deepseek-chat",
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=2,
                total_tokens=14,
            ),
        )

    provider = object.__new__(DeepSeekProvider)
    create = AsyncMock(return_value=sdk_stream())
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    events = asyncio.run(collect_stream(provider, build_request()))
    completed = events[-1]

    assert [event.delta for event in events[:-1]] == ["会议", "完成"]
    assert isinstance(completed, ChatStreamCompletedEvent)
    assert completed.response.content == "会议完成"
    assert completed.response.finish_reason == "stop"
    assert completed.response.total_tokens == 14
    request_options = create.await_args.kwargs
    assert request_options["stream"] is True
    assert request_options["stream_options"] == {"include_usage": True}


def test_deepseek_chat_preserves_finish_reason_and_disables_thinking() -> None:
    """非流式响应也必须保留 length，并把关闭思考配置真实传给厂商接口。"""
    sdk_response = SimpleNamespace(
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="只生成了一半"),
                finish_reason="length",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=20,
            completion_tokens=64,
            total_tokens=84,
        ),
    )
    provider = object.__new__(DeepSeekProvider)
    provider.thinking_enabled = False
    create = AsyncMock(return_value=sdk_response)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    response = asyncio.run(provider.chat(build_request()))

    assert response.finish_reason == "length"
    assert response.content == "只生成了一半"
    assert create.await_args.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_provider_factory_applies_default_deepseek_thinking_configuration(
    monkeypatch,
) -> None:
    """普通聊天 Provider 必须显式采用配置值，不能把思考模式留给厂商默认决定。"""
    provider = object()
    deepseek_factory = Mock(return_value=provider)
    monkeypatch.setattr(provider_factory_module, "DeepSeekProvider", deepseek_factory)
    monkeypatch.setattr(provider_factory_module.settings, "default_provider", "deepseek")

    result = ProviderFactory.create()

    assert result is provider
    deepseek_factory.assert_called_once_with(
        thinking_enabled=provider_factory_module.settings.deepseek.thinking_enabled
    )


def test_deepseek_stream_converts_sdk_exception_to_safe_error() -> None:
    """SDK 异常只能形成稳定错误事件，不能向调用方泄露原始异常正文。"""
    provider = object.__new__(DeepSeekProvider)
    create = AsyncMock(side_effect=TimeoutError("包含内部地址的异常正文"))
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    events = asyncio.run(collect_stream(provider, build_request()))

    assert len(events) == 1
    assert isinstance(events[0], ChatStreamErrorEvent)
    assert events[0].error_code == "LLM_STREAM_ERROR"
    assert events[0].error_message == "模型流式调用失败"
    assert "内部地址" not in events[0].error_message
