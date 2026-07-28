import inspect
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from app.common.enums import MessageRole, MessageStatus
from app.infrastructure.database.session import to_async_database_url
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.api.conversation_router import (
    create_conversation,
    get_messages,
)
from app.modules.conversation.service.conversation_service import ConversationService


def test_postgresql_url_uses_async_psycopg_dialect() -> None:
    assert (
        to_async_database_url("postgresql://user:password@localhost:5432/assistant")
        == "postgresql+psycopg://user:password@localhost:5432/assistant"
    )
    assert (
        to_async_database_url(
            "postgresql+psycopg://user:password@localhost:5432/assistant"
        )
        == "postgresql+psycopg://user:password@localhost:5432/assistant"
    )


def test_database_backed_conversation_handlers_are_async() -> None:
    assert inspect.iscoroutinefunction(create_conversation)
    assert inspect.iscoroutinefunction(get_messages)


def test_memory_failure_falls_back_to_database_history() -> None:
    message = MessageEntity(
        conversation_id=uuid4(),
        role=MessageRole.USER,
        content="上次会议的结论是什么？",
        model_name="user",
        status=MessageStatus.COMPLETED,
    )
    service = object.__new__(ConversationService)
    service.memory_manager = FailingMemory()
    service.message_repository = type(
        "MessageRepositoryStub",
        (),
        {"list_completed_by_conversation": AsyncMock(return_value=[message])},
    )()
    service.history_mapper = HistoryMapper()

    history = asyncio.run(service._get_history(message.conversation_id))
    asyncio.run(service._save_to_memory(message.conversation_id, message))

    assert history[0].content == "上次会议的结论是什么？"


class FailingMemory:
    async def get_context(self, conversation_id: str):
        raise ConnectionError("Redis is unavailable")

    async def save_message(self, conversation_id: str, message: object) -> None:
        raise ConnectionError("Redis is unavailable")
