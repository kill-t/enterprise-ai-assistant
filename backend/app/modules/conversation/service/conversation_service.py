import asyncio
from uuid import UUID

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
    def __init__(
        self,
        db: AsyncSession,
        memory_manager: MemoryManager,
    ):
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
        entity = await self.repository.create(
            user_id=user_id,
            conversation=conversation,
        )
        await self._commit()

        app_logger.info(
            "Create conversation user_id=%s conversation_id=%s",
            user_id,
            entity.id,
        )
        return ConversationResponse.model_validate(entity)

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
    ) -> MessageResponse:
        app_logger.info("Receive message conversation_id=%s", conversation_id)
        conversation = await self.repository.get_by_id(conversation_id)

        if conversation is None:
            app_logger.warning("Conversation not found conversation_id=%s", conversation_id)
            raise BusinessException(
                code=ErrorCode.CONVERSATION_NOT_FOUND,
                message="Conversation not found",
                status_code=404,
            )

        history_messages = await self._get_history(conversation_id)
        request = self.prompt_builder.build(
            system_prompt=conversation.system_prompt,
            history=self.context_manager.build_history(history_messages),
            user_message=content,
            temperature=conversation.temperature,
        )

        user_message = MessageEntity(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            model_name="user",
            status=MessageStatus.COMPLETED,
        )
        await self.message_repository.create(user_message)
        await self._commit()
        await self._save_to_memory(conversation_id, user_message)

        # Providers are still synchronous in this module. Run them away from the
        # event loop until the dedicated async-provider module replaces them.
        assistant_response = await asyncio.to_thread(self.provider.chat, request)
        assistant_message = MessageEntity(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=assistant_response.content,
            model_name=assistant_response.model,
            prompt_tokens=assistant_response.prompt_tokens,
            completion_tokens=assistant_response.completion_tokens,
            total_tokens=assistant_response.total_tokens,
            status=MessageStatus.COMPLETED,
        )
        await self.message_repository.create(assistant_message)
        await self._commit()
        await self._save_to_memory(conversation_id, assistant_message)

        app_logger.info("Assistant message saved conversation_id=%s", conversation_id)
        return MessageResponse.model_validate(assistant_message)

    async def get_messages(
        self,
        conversation_id: UUID,
    ) -> list[MessageResponse]:
        entities = await self.message_repository.list_by_conversation(conversation_id)
        return [MessageResponse.model_validate(entity) for entity in entities]

    async def _commit(self) -> None:
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

    async def _get_history(self, conversation_id: UUID):
        try:
            memory_messages = await self.memory_manager.get_context(str(conversation_id))
        except Exception:
            app_logger.warning(
                "Memory unavailable; loading history from database conversation_id=%s",
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
        entities = await self.message_repository.list_by_conversation(conversation_id)
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
        entities = await self.message_repository.list_by_conversation(conversation_id)
        for entity in entities:
            await self._save_to_memory(conversation_id, entity)

    async def _save_to_memory(
        self,
        conversation_id: UUID,
        entity: MessageEntity,
    ) -> None:
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
                "Memory write failed; database remains authoritative conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
