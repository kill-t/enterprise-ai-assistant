import asyncio
from datetime import datetime, timezone
from uuid import UUID

from openai import APITimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.infrastructure.llm.context_manager import (
    ContextBudgetExceededError,
    ContextManager,
)
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.infrastructure.llm.schemas import ChatMessage, ChatRequest
from app.infrastructure.memory.memory_manager import MemoryManager
from app.infrastructure.memory.schemas import MemoryMessage
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.repository.message_repository import MessageRepository
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
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
        context_settings = ProviderFactory.get_context_settings()
        self.context_manager = ContextManager(
            context_window_tokens=context_settings.context_window_tokens,
            max_output_tokens=context_settings.max_output_tokens,
            token_safety_margin=context_settings.token_safety_margin,
        )
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

    async def list_conversations(
        self,
        user_id: UUID,
        include_archived: bool = False,
    ) -> list[ConversationResponse]:
        """返回当前用户的会话列表，并默认隐藏已归档会话。"""
        entities = await self.repository.list_by_user(
            user_id=user_id,
            include_archived=include_archived,
        )
        return [ConversationResponse.model_validate(entity) for entity in entities]

    async def get(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> ConversationResponse:
        """返回属于当前用户的指定会话。"""
        entity = await self._get_owned_conversation(user_id, conversation_id)
        return ConversationResponse.model_validate(entity)

    async def update(
        self,
        user_id: UUID,
        conversation_id: UUID,
        changes: ConversationUpdate,
    ) -> ConversationResponse:
        """修改当前用户会话的标题或归档状态并提交事务。"""
        entity = await self._get_owned_conversation(user_id, conversation_id)
        update_fields = changes.model_dump(exclude_unset=True)
        for field_name, value in update_fields.items():
            setattr(entity, field_name, value)
        await self.repository.update(entity)
        await self._commit()
        app_logger.info(
            "会话已更新 user_id=%s conversation_id=%s fields=%s",
            user_id,
            conversation_id,
            sorted(update_fields),
        )
        return ConversationResponse.model_validate(entity)

    async def delete(self, user_id: UUID, conversation_id: UUID) -> None:
        """删除当前用户的会话及消息，并尽力清理 Redis 短期记忆。"""
        entity = await self._get_owned_conversation(user_id, conversation_id)
        await self.repository.delete(entity)
        await self._commit()
        await self._delete_memory(conversation_id)
        app_logger.info(
            "会话已删除 user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )

    async def send_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> MessageResponse:
        """保存用户问题、调用模型并持久化助手消息状态。"""
        app_logger.info("收到用户消息 conversation_id=%s", conversation_id)
        conversation = await self._get_owned_conversation(user_id, conversation_id)

        history_messages = await self._get_history(conversation_id)
        request = self._build_chat_request(
            conversation=conversation,
            history=history_messages,
            user_message=content,
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

        return await self._generate_assistant_response(
            conversation_id=conversation_id,
            request=request,
            assistant_message=assistant_message,
        )

    async def retry_failed_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> MessageResponse:
        """将失败助手消息恢复为 pending，并使用原问题重新调用模型。"""
        conversation = await self._get_owned_conversation(user_id, conversation_id)

        assistant_message = await self.message_repository.get_by_id(message_id)
        if assistant_message is None or assistant_message.conversation_id != conversation_id:
            raise BusinessException(
                code=ErrorCode.MESSAGE_NOT_FOUND,
                message="消息不存在",
                status_code=404,
            )

        if (
            assistant_message.role != MessageRole.ASSISTANT
            or assistant_message.status != MessageStatus.FAILED
        ):
            raise BusinessException(
                code=ErrorCode.MESSAGE_NOT_RETRYABLE,
                message="只有失败的助手消息可以重试",
                status_code=409,
            )

        original_user_message = await self._find_original_user_message(
            conversation_id=conversation_id,
            assistant_message_id=message_id,
        )

        # 原用户消息会作为当前问题传入，因此必须从历史中排除，避免重复出现。
        history_messages = await self._get_history(
            conversation_id,
            exclude_message_id=original_user_message.id,
        )
        request = self._build_chat_request(
            conversation=conversation,
            history=history_messages,
            user_message=original_user_message.content,
        )

        # 复用原记录可保证前端消息 ID 与审计记录稳定，不会制造重复助手消息。
        assistant_message.content = ""
        assistant_message.status = MessageStatus.PENDING
        assistant_message.error_code = None
        assistant_message.error_message = None
        assistant_message.finished_at = None
        assistant_message.prompt_tokens = 0
        assistant_message.completion_tokens = 0
        assistant_message.total_tokens = 0
        await self.message_repository.update(assistant_message)
        await self._commit()

        app_logger.info(
            "开始重试失败助手消息 conversation_id=%s message_id=%s",
            conversation_id,
            message_id,
        )
        return await self._generate_assistant_response(
            conversation_id=conversation_id,
            request=request,
            assistant_message=assistant_message,
        )

    async def _generate_assistant_response(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        assistant_message: MessageEntity,
    ) -> MessageResponse:
        """调用模型，并将成功或失败结果写回指定助手消息。"""

        try:
            # Provider 在基础设施层处理厂商 SDK 适配，Service 只依赖异步统一接口。
            assistant_response = await self.provider.chat(request)
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

    def _build_chat_request(
        self,
        conversation: ConversationEntity,
        history: list[ChatMessage],
        user_message: str,
    ) -> ChatRequest:
        """按模型预算裁剪完整轮次，并构造 Provider 使用的统一请求。"""
        system_prompt = self.prompt_builder.resolve_system_prompt(
            conversation.system_prompt
        )
        try:
            selected_history = self.context_manager.build_history(
                history=history,
                system_prompt=system_prompt,
                user_message=user_message,
                model_name=conversation.model_name,
            )
        except ContextBudgetExceededError as exc:
            raise BusinessException(
                code=ErrorCode.CONTEXT_WINDOW_EXCEEDED,
                message="当前问题过长，请缩短内容后重试",
                status_code=422,
            ) from exc

        return self.prompt_builder.build(
            system_prompt=system_prompt,
            history=selected_history,
            user_message=user_message,
            temperature=conversation.temperature,
            model_name=conversation.model_name,
        )

    async def get_messages(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> list[MessageResponse]:
        """返回全部消息，使前端能够展示 pending 和 failed 状态。"""
        await self._get_owned_conversation(user_id, conversation_id)
        entities = await self.message_repository.list_by_conversation(conversation_id)
        return [MessageResponse.model_validate(entity) for entity in entities]

    async def _get_owned_conversation(
        self,
        user_id: UUID,
        conversation_id: UUID,
    ) -> ConversationEntity:
        """执行统一所有权查询，并把不存在和跨用户访问统一为 404。"""
        conversation = await self.repository.get_by_id_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            app_logger.warning(
                "会话不存在或不属于当前用户 user_id=%s conversation_id=%s",
                user_id,
                conversation_id,
            )
            raise BusinessException(
                code=ErrorCode.CONVERSATION_NOT_FOUND,
                message="会话不存在",
                status_code=404,
            )
        return conversation

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

    async def _get_history(
        self,
        conversation_id: UUID,
        exclude_message_id: UUID | None = None,
    ):
        """优先读取 Redis；必要时回退数据库，并可排除正在重试的原问题。"""
        try:
            memory_messages = await self.memory_manager.get_context(str(conversation_id))
        except Exception:
            app_logger.warning(
                "Redis 记忆不可用，改为从数据库读取历史 conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
            return await self._get_history_from_database(
                conversation_id,
                exclude_message_id=exclude_message_id,
            )

        if memory_messages:
            filtered_messages = [
                message
                for message in memory_messages
                if exclude_message_id is None or message.id != exclude_message_id
            ]
            return self.history_mapper.to_chat_messages(filtered_messages)

        database_history = await self._get_history_from_database(
            conversation_id,
            exclude_message_id=exclude_message_id,
        )
        if database_history:
            await self._warm_memory(conversation_id)
        return database_history

    async def _get_history_from_database(
        self,
        conversation_id: UUID,
        exclude_message_id: UUID | None = None,
    ):
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
                if exclude_message_id is None or entity.id != exclude_message_id
            ]
        )

    async def _find_original_user_message(
        self,
        conversation_id: UUID,
        assistant_message_id: UUID,
    ) -> MessageEntity:
        """查找失败助手消息前最近的已完成用户消息，作为重试的原问题。"""
        messages = await self.message_repository.list_by_conversation(conversation_id)
        assistant_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.id == assistant_message_id
            ),
            None,
        )
        if assistant_index is not None:
            for message in reversed(messages[:assistant_index]):
                if (
                    message.role == MessageRole.USER
                    and message.status == MessageStatus.COMPLETED
                ):
                    return message

        raise BusinessException(
            code=ErrorCode.MESSAGE_NOT_RETRYABLE,
            message="未找到可用于重试的原始用户消息",
            status_code=409,
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

    async def _delete_memory(self, conversation_id: UUID) -> None:
        """尽力清理已删除会话的 Redis 缓存，缓存故障不回滚数据库删除。"""
        try:
            await self.memory_manager.delete_context(str(conversation_id))
        except Exception:
            app_logger.warning(
                "Redis 记忆删除失败，数据库会话已删除 conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
