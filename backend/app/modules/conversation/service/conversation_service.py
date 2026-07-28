import asyncio
from datetime import datetime, timezone
from uuid import UUID

from openai import APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.infrastructure.llm.context_manager import ContextManager
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.infrastructure.memory.memory_manager import MemoryManager
from app.infrastructure.memory.schemas import MemoryMessage
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.repository.message_repository import MessageRepository
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
)
from app.modules.conversation.schema.message_schema import MessageResponse


class ConversationService:
    """编排会话持久化、模型调用、消息状态和短期记忆。"""

    def __init__(
        self,
        db: AsyncSession,
        memory_manager: MemoryManager,
    ):
        """组装当前请求使用的仓储、Provider 和上下文组件。"""
        self.db = db
        self.repository = ConversationRepository(db)
        self.message_repository = MessageRepository(db)
        self.memory_manager = memory_manager
        self.provider = ProviderFactory.create()
        self.prompt_builder = PromptBuilder()
        self.context_manager = ContextManager()
        self.history_mapper = HistoryMapper()

    async def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationResponse:
        """创建会话并由 Service 控制事务提交。"""
        entity = await self.repository.create(
            user_id=user_id,
            conversation=conversation,
        )
        await self._commit()

        app_logger.info("已创建会话 user_id=%s conversation_id=%s", user_id, entity.id)
        return ConversationResponse.model_validate(entity)

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
    ) -> MessageResponse:
        """保存用户问题、调用模型并持久化助手消息状态。"""
        app_logger.info("收到用户消息 conversation_id=%s", conversation_id)
        conversation = await self.repository.get_by_id(conversation_id)

        if conversation is None:
            app_logger.warning("会话不存在 conversation_id=%s", conversation_id)
            raise BusinessException(
                code=ErrorCode.CONVERSATION_NOT_FOUND,
                message="会话不存在",
                status_code=404,
            )

        history_messages = await self._get_history(conversation_id)
        request = self.prompt_builder.build(
            system_prompt=conversation.system_prompt,
            history=self.context_manager.build_history(history_messages),
            user_message=content,
            temperature=conversation.temperature,
        )

        # 用户问题已经被服务端接收，是需要独立持久化的事实记录。
        user_message = MessageEntity(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            model_name="user",
            status=MessageStatus.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        )
        await self.message_repository.create(user_message)
        await self._commit()
        await self._save_to_memory(conversation_id, user_message)

        # 调用模型前先创建 pending 记录，确保超时或异常也能被追踪。
        assistant_message = MessageEntity(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content="",
            model_name=conversation.model_name,
            status=MessageStatus.PENDING,
        )
        await self.message_repository.create(assistant_message)
        await self._commit()

        try:
            # 当前 Provider 仍为同步实现，在线程中执行以避免阻塞事件循环。
            assistant_response = await asyncio.to_thread(self.provider.chat, request)
        except Exception as exc:
            await self._mark_assistant_failed(assistant_message, exc)

        assistant_message.content = assistant_response.content
        assistant_message.model_name = assistant_response.model
        assistant_message.prompt_tokens = assistant_response.prompt_tokens
        assistant_message.completion_tokens = assistant_response.completion_tokens
        assistant_message.total_tokens = assistant_response.total_tokens
        assistant_message.status = MessageStatus.COMPLETED
        assistant_message.finished_at = datetime.now(timezone.utc)
        await self.message_repository.update(assistant_message)
        await self._commit()
        await self._save_to_memory(conversation_id, assistant_message)

        app_logger.info("助手消息已保存 conversation_id=%s", conversation_id)
        return MessageResponse.model_validate(assistant_message)

    async def get_messages(
        self,
        conversation_id: UUID,
    ) -> list[MessageResponse]:
        """返回全部消息，使前端能够展示 pending 和 failed 状态。"""
        entities = await self.message_repository.list_by_conversation(conversation_id)
        return [MessageResponse.model_validate(entity) for entity in entities]

    async def _commit(self) -> None:
        """提交当前事务；提交失败时回滚 Session 并保留原异常。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def _mark_assistant_failed(
        self,
        assistant_message: MessageEntity,
        exc: Exception,
    ) -> None:
        """将助手消息更新为失败状态，并抛出安全的业务异常。"""
        error = self._to_provider_exception(exc)
        assistant_message.status = MessageStatus.FAILED
        assistant_message.error_code = error.code.value
        assistant_message.error_message = error.message
        assistant_message.finished_at = datetime.now(timezone.utc)
        await self.message_repository.update(assistant_message)
        await self._commit()
        app_logger.warning(
            "模型调用失败 conversation_id=%s error_code=%s",
            assistant_message.conversation_id,
            error.code.value,
            exc_info=True,
        )
        raise error

    def _to_provider_exception(self, exc: Exception) -> BusinessException:
        """把 Provider 异常映射为稳定错误码和用户可读中文提示。"""
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError, APITimeoutError)):
            return BusinessException(
                code=ErrorCode.MODEL_TIMEOUT,
                message="模型响应超时，请稍后重试",
                status_code=504,
            )

        return BusinessException(
            code=ErrorCode.MODEL_REQUEST_FAILED,
            message="模型服务调用失败，请稍后重试",
            status_code=502,
        )

    async def _get_history(self, conversation_id: UUID):
        """优先读取 Redis；缓存不可用或为空时回退到 PostgreSQL。"""
        try:
            memory_messages = await self.memory_manager.get_context(str(conversation_id))
        except Exception:
            app_logger.warning(
                "Redis 记忆不可用，改为从数据库读取历史 conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
            return await self._get_history_from_database(conversation_id)

        if memory_messages:
            return self.history_mapper.to_chat_messages(memory_messages)

        database_history = await self._get_history_from_database(conversation_id)
        if database_history:
            await self._warm_memory(conversation_id)
        return database_history

    async def _get_history_from_database(self, conversation_id: UUID):
        """从数据库读取已完成消息并转换为模型历史。"""
        entities = await self.message_repository.list_completed_by_conversation(
            conversation_id
        )
        return self.history_mapper.to_chat_messages(
            [
                MemoryMessage(
                    id=entity.id,
                    role=entity.role.value,
                    content=entity.content,
                    created_at=entity.created_at,
                )
                for entity in entities
            ]
        )

    async def _warm_memory(self, conversation_id: UUID) -> None:
        """将数据库中的已完成消息逐条回填到空缓存。"""
        entities = await self.message_repository.list_completed_by_conversation(
            conversation_id
        )
        for entity in entities:
            await self._save_to_memory(conversation_id, entity)

    async def _save_to_memory(
        self,
        conversation_id: UUID,
        entity: MessageEntity,
    ) -> None:
        """尽力写入 Redis；缓存故障不得覆盖数据库成功结果。"""
        try:
            await self.memory_manager.save_message(
                str(conversation_id),
                MemoryMessage(
                    id=entity.id,
                    role=entity.role.value,
                    content=entity.content,
                    created_at=entity.created_at,
                ),
            )
        except Exception:
            app_logger.warning(
                "Redis 记忆写入失败，数据库记录仍然有效 conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
