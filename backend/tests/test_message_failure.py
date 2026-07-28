import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.infrastructure.llm.context_manager import ContextManager
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.schema.message_schema import MessageResponse
from app.modules.conversation.service.conversation_service import ConversationService


class FailingProvider:
    """固定抛出超时异常的模型 Provider。"""

    def chat(self, request):
        """模拟模型在生成回复阶段超时。"""
        raise TimeoutError("模型响应超时")


class RecordingMemoryManager:
    """记录缓存写入的测试替身，用于确认失败消息不会进入上下文。"""

    def __init__(self) -> None:
        """初始化已保存消息列表。"""
        self.saved_messages = []

    async def get_context(self, conversation_id: str):
        """返回空历史，隔离既有上下文对测试的影响。"""
        return []

    async def save_message(self, conversation_id: str, message) -> None:
        """记录服务尝试写入缓存的消息。"""
        self.saved_messages.append(message)


def test_model_timeout_persists_failed_assistant_message() -> None:
    """模型超时后应保留用户消息，并把占位助手消息更新为失败状态。"""
    conversation_id = uuid4()
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    message_repository = SimpleNamespace(
        create=AsyncMock(side_effect=lambda message: message),
        update=AsyncMock(side_effect=lambda message: message),
        list_completed_by_conversation=AsyncMock(return_value=[]),
    )
    memory_manager = RecordingMemoryManager()
    service = object.__new__(ConversationService)
    service.db = db
    service.repository = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(
                system_prompt=None,
                temperature=0.7,
                model_name="mock",
            )
        )
    )
    service.message_repository = message_repository
    service.memory_manager = memory_manager
    service.provider = FailingProvider()
    service.prompt_builder = PromptBuilder()
    service.context_manager = ContextManager()
    service.history_mapper = HistoryMapper()

    async def scenario() -> BusinessException:
        """捕获服务转换后的业务异常，供同步测试断言。"""
        try:
            await service.send_message(conversation_id, "整理会议待办")
        except BusinessException as exc:
            return exc
        raise AssertionError("模型超时必须转换为业务异常")

    error = asyncio.run(scenario())
    user_message, assistant_message = [
        call.args[0] for call in message_repository.create.await_args_list
    ]

    assert error.code == ErrorCode.MODEL_TIMEOUT
    assert error.status_code == 504
    assert user_message.status == MessageStatus.COMPLETED
    assert assistant_message.status == MessageStatus.FAILED
    assert assistant_message.error_code == ErrorCode.MODEL_TIMEOUT.value
    assert assistant_message.error_message == "模型响应超时，请稍后重试"
    assert assistant_message.finished_at is not None
    assert [message.role for message in memory_manager.saved_messages] == ["user"]
    assert db.commit.await_count == 3


def test_failed_message_response_exposes_safe_error_details() -> None:
    """失败消息的响应模型应暴露可安全展示的错误码和提示。"""
    message = MessageEntity(
        id=uuid4(),
        conversation_id=uuid4(),
        role=MessageRole.ASSISTANT,
        content="",
        model_name="mock",
        status=MessageStatus.FAILED,
        error_code=ErrorCode.MODEL_TIMEOUT.value,
        error_message="模型响应超时，请稍后重试",
        finished_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    response = MessageResponse.model_validate(message)

    assert response.status == MessageStatus.FAILED
    assert response.error_code == ErrorCode.MODEL_TIMEOUT.value
