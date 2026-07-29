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
from app.infrastructure.llm.schemas import ChatResponse
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.schema.message_schema import MessageResponse
from app.modules.conversation.service.conversation_service import ConversationService


class FailingProvider:
    """固定抛出超时异常的模型 Provider。"""

    async def chat(self, request):
        """模拟异步模型在生成回复阶段超时。"""
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


class SuccessfulProvider:
    """返回固定成功结果的模型 Provider，用于验证原消息的原位更新。"""

    def __init__(self) -> None:
        """初始化调用记录，便于断言原问题没有重复进入上下文。"""
        self.requests = []

    async def chat(self, request):
        """记录异步模型请求并返回带 token 统计的固定结果。"""
        self.requests.append(request)
        return ChatResponse(
            content="已重新生成会议待办",
            model="mock-retry",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )


def build_retry_service(
    conversation_id,
    assistant_message: MessageEntity,
    provider,
) -> tuple[ConversationService, SimpleNamespace, RecordingMemoryManager]:
    """构造重试场景所需的 Service 与可观察的仓储、缓存替身。"""
    original_user_message = MessageEntity(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="整理会议待办",
        model_name="user",
        status=MessageStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    message_repository = SimpleNamespace(
        create=AsyncMock(side_effect=lambda message: message),
        get_by_id=AsyncMock(return_value=assistant_message),
        update=AsyncMock(side_effect=lambda message: message),
        list_by_conversation=AsyncMock(
            return_value=[original_user_message, assistant_message]
        ),
        list_completed_by_conversation=AsyncMock(return_value=[original_user_message]),
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
    service.provider = provider
    service.prompt_builder = PromptBuilder()
    service.context_manager = ContextManager()
    service.history_mapper = HistoryMapper()
    return service, message_repository, memory_manager


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


def test_retry_reuses_failed_assistant_message_and_excludes_original_question() -> None:
    """重试成功时必须复用原助手消息，且原问题只作为当前问题传给模型一次。"""
    conversation_id = uuid4()
    failed_message = MessageEntity(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="",
        model_name="mock",
        status=MessageStatus.FAILED,
        error_code=ErrorCode.MODEL_TIMEOUT.value,
        error_message="模型响应超时，请稍后重试",
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    provider = SuccessfulProvider()
    service, message_repository, memory_manager = build_retry_service(
        conversation_id,
        failed_message,
        provider,
    )

    response = asyncio.run(
        service.retry_failed_message(conversation_id, failed_message.id)
    )

    assert response.id == failed_message.id
    assert failed_message.status == MessageStatus.COMPLETED
    assert failed_message.content == "已重新生成会议待办"
    assert failed_message.error_code is None
    assert failed_message.error_message is None
    assert message_repository.create.await_count == 0
    assert [message.role for message in memory_manager.saved_messages] == ["assistant"]
    assert [message.content for message in provider.requests[0].messages].count(
        "整理会议待办"
    ) == 1


def test_retry_failure_marks_the_same_message_as_failed_again() -> None:
    """重试再次超时时，应把同一条助手消息重新标记为失败。"""
    conversation_id = uuid4()
    failed_message = MessageEntity(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="旧失败内容",
        model_name="mock",
        status=MessageStatus.FAILED,
        error_code=ErrorCode.MODEL_REQUEST_FAILED.value,
        error_message="模型服务调用失败，请稍后重试",
        created_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    service, message_repository, memory_manager = build_retry_service(
        conversation_id,
        failed_message,
        FailingProvider(),
    )

    async def scenario() -> BusinessException:
        """捕获重试流程抛出的业务异常，保留后续状态断言。"""
        try:
            await service.retry_failed_message(conversation_id, failed_message.id)
        except BusinessException as exc:
            return exc
        raise AssertionError("重试超时必须转换为业务异常")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.MODEL_TIMEOUT
    assert failed_message.id == message_repository.get_by_id.await_args.args[0]
    assert failed_message.status == MessageStatus.FAILED
    assert failed_message.error_code == ErrorCode.MODEL_TIMEOUT.value
    assert failed_message.content == ""
    assert memory_manager.saved_messages == []


def test_only_failed_assistant_message_can_be_retried() -> None:
    """处理中或已完成消息即使被重复点击，也必须被服务层拒绝。"""
    conversation_id = uuid4()
    pending_message = MessageEntity(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="",
        model_name="mock",
        status=MessageStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    service, message_repository, _ = build_retry_service(
        conversation_id,
        pending_message,
        SuccessfulProvider(),
    )

    async def scenario() -> BusinessException:
        """捕获状态冲突异常，验证重复点击不会进入模型调用。"""
        try:
            await service.retry_failed_message(conversation_id, pending_message.id)
        except BusinessException as exc:
            return exc
        raise AssertionError("pending 消息必须拒绝重试")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.MESSAGE_NOT_RETRYABLE
    assert error.status_code == 409
    assert message_repository.list_by_conversation.await_count == 0


def test_retry_rejects_message_from_another_conversation() -> None:
    """消息不属于目标会话时必须返回未找到，避免跨会话访问。"""
    conversation_id = uuid4()
    another_conversation_id = uuid4()
    foreign_message = MessageEntity(
        id=uuid4(),
        conversation_id=another_conversation_id,
        role=MessageRole.ASSISTANT,
        content="",
        model_name="mock",
        status=MessageStatus.FAILED,
        created_at=datetime.now(timezone.utc),
    )
    service, _, _ = build_retry_service(
        conversation_id,
        foreign_message,
        SuccessfulProvider(),
    )

    async def scenario() -> BusinessException:
        """捕获跨会话访问时的未找到异常。"""
        try:
            await service.retry_failed_message(conversation_id, foreign_message.id)
        except BusinessException as exc:
            return exc
        raise AssertionError("跨会话消息必须拒绝重试")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.MESSAGE_NOT_FOUND
    assert error.status_code == 404
