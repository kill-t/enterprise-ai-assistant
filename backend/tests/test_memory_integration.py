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
    def __init__(self) -> None:
        self.messages: list[MemoryMessage] = []

    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:
        self.messages.append(message)

    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        return self.messages


class FakeConversationService:
    def __init__(self) -> None:
        self.received: tuple[object, str] | None = None

    async def send_message(
        self,
        conversation_id: object,
        content: str,
    ) -> MessageResponse:
        self.received = (conversation_id, content)
        return MessageResponse(
            id=uuid4(),
            role="assistant",
            content="已收到",
            model_name="mock",
            created_at="2026-07-27T00:00:00Z",
        )


def test_memory_manager_round_trips_messages() -> None:
    memory = FakeMemory()
    manager = MemoryManager(short_term_memory=memory)

    async def scenario() -> None:
        await manager.save_message(
            "conversation-1",
            MemoryMessage(role="user", content="你好"),
        )
        assert await manager.get_context("conversation-1") == [
            MemoryMessage(role="user", content="你好")
        ]

    asyncio.run(scenario())


def test_conversation_service_dependency_injects_memory_manager() -> None:
    db = Mock()
    memory_manager = Mock(spec=MemoryManager)

    with patch(
        "app.common.dependencies.ConversationService"
    ) as service_class:
        get_conversation_service(db=db, memory_manager=memory_manager)

    service_class.assert_called_once_with(db, memory_manager)


def test_send_message_router_is_async_and_awaits_service() -> None:
    assert inspect.iscoroutinefunction(send_message)
    service = FakeConversationService()
    conversation_id = uuid4()

    response = asyncio.run(
        send_message(
            conversation_id=conversation_id,
            request=MessageCreate(content="帮我总结会议资料"),
            service=service,
        )
    )

    assert response.content == "已收到"
    assert service.received == (conversation_id, "帮我总结会议资料")
