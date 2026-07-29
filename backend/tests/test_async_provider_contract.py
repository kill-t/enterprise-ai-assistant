import asyncio
import inspect

from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.infrastructure.llm.schemas import ChatMessage, ChatRequest, ChatRole


def test_base_provider_chat_is_an_async_contract() -> None:
    """Provider 抽象接口必须声明为异步，避免业务层依赖同步实现细节。"""
    assert inspect.iscoroutinefunction(BaseChatProvider.chat)


def test_mock_provider_returns_response_through_async_contract() -> None:
    """Mock Provider 应通过 await 返回稳定结果，供本地开发和测试使用。"""
    request = ChatRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="你好")]
    )

    response = asyncio.run(MockProvider().chat(request))

    assert response.model == "mock"
    assert response.content == "Hello, I'm Enterprise AI Assistant."
