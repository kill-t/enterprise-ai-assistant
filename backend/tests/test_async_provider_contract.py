import asyncio
import inspect
import json

from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponseFormat,
    ChatRole,
)
from app.modules.conversation.schema.conversation_summary_schema import (
    ConversationSummaryContent,
)


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


def test_mock_provider_returns_valid_structured_summary_json() -> None:
    """本地 Mock Provider 应支持摘要 JSON 契约，避免开发环境依赖真实密钥。"""
    request = ChatRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="生成摘要")],
        response_format=ChatResponseFormat.JSON_OBJECT,
        metadata={"operation": "conversation_summary"},
    )

    response = asyncio.run(MockProvider().chat(request))
    parsed = ConversationSummaryContent.model_validate(json.loads(response.content))

    assert parsed.user_goal_and_context
