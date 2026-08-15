import asyncio
import inspect
from unittest.mock import Mock, patch
from uuid import uuid4

from app.common.dependencies import get_conversation_service
from app.infrastructure.memory.memory_manager import MemoryManager
from app.infrastructure.memory.schemas import MemoryMessage
from app.modules.conversation.api.conversation_router import send_message
from app.modules.conversation.schema.message_schema import (
    MessageCreate,
    MessageResponse,
)


class FakeMemory:
    """用于单元测试的内存版短期记忆实现。"""

    def __init__(self) -> None:
        """初始化消息列表，替代真实 Redis。"""
        self.messages: list[MemoryMessage] = []

    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:
        """将消息追加到测试列表。"""
        self.messages.append(message)

    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        """返回当前测试列表中的全部消息。"""
        return self.messages

    async def delete_messages(self, conversation_id: str) -> None:
        """清空测试列表，模拟删除 Redis 会话键。"""
        self.messages.clear()


class FakeConversationService:
    """记录路由调用参数的会话服务替身。"""

    def __init__(self) -> None:
        """初始化尚未收到调用的状态。"""
        self.received: tuple[object, object, str] | None = None

    async def send_message(
        self,
        user_id: object,
        conversation_id: object,
        content: str,
    ) -> MessageResponse:
        """记录输入并返回固定助手消息，隔离真实业务实现。"""
        self.received = (user_id, conversation_id, content)
        return MessageResponse(
            id=uuid4(),
            role="assistant",
            content="已收到",
            model_name="mock",
            status="completed",
            error_code=None,
            error_message=None,
            created_at="2026-07-27T00:00:00Z",
            finished_at="2026-07-27T00:00:00Z",
        )


def test_memory_manager_round_trips_messages() -> None:
    """MemoryManager 应能通过统一接口保存并读取消息。"""
    memory = FakeMemory()
    manager = MemoryManager(short_term_memory=memory)

    async def scenario() -> None:
        """在同一事件循环中完成异步缓存读写断言。"""
        await manager.save_message(
            "conversation-1",
            MemoryMessage(role="user", content="你好"),
        )
        assert await manager.get_context("conversation-1") == [
            MemoryMessage(role="user", content="你好")
        ]

    asyncio.run(scenario())


def test_memory_manager_deletes_conversation_context() -> None:
    """MemoryManager 应通过统一接口删除指定会话的全部短期消息。"""
    memory = FakeMemory()
    memory.messages.append(MemoryMessage(role="user", content="待删除"))
    manager = MemoryManager(short_term_memory=memory)

    asyncio.run(manager.delete_context("conversation-1"))

    assert memory.messages == []


def test_conversation_service_dependency_injects_memory_manager() -> None:
    """依赖工厂必须同时注入数据库、记忆管理器和在线审计 Provider。"""
    db = Mock()
    memory_manager = Mock(spec=MemoryManager)
    audited_provider = Mock()

    with (
        patch("app.common.dependencies.ConversationService") as service_class,
        patch(
            "app.common.dependencies.create_audited_default_provider",
            return_value=audited_provider,
        ) as provider_factory,
    ):
        get_conversation_service(db=db, memory_manager=memory_manager)

    provider_factory.assert_called_once_with()
    service_class.assert_called_once_with(
        db,
        memory_manager,
        provider=audited_provider,
    )


def test_send_message_router_is_async_and_awaits_service() -> None:
    """发送消息路由应等待异步服务，并原样传递会话编号和内容。"""
    assert inspect.iscoroutinefunction(send_message)
    service = FakeConversationService()
    conversation_id = uuid4()
    current_user = type("CurrentUser", (), {"id": uuid4()})()

    response = asyncio.run(
        send_message(
            conversation_id=conversation_id,
            request=MessageCreate(content="帮我总结会议资料"),
            service=service,
            current_user=current_user,
        )
    )

    assert response.content == "已收到"
    assert service.received == (
        current_user.id,
        conversation_id,
        "帮我总结会议资料",
    )
