from uuid import UUID

from sqlalchemy.orm import Session

from app.common.enums import MessageRole, MessageStatus
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.infrastructure.llm.schemas import ChatMessage, ChatRole
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.repository.message_repository import (
    MessageRepository,
)
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
)
from app.infrastructure.llm.context_manager import ContextManager
from app.infrastructure.llm.history_mapper import HistoryMapper
from app.modules.conversation.schema.message_schema import MessageResponse


class ConversationService:

    def __init__(self, db: Session):
        self.repository = ConversationRepository(db)
        self.message_repository = MessageRepository(db)
        self.provider = ProviderFactory.create()
        self.prompt_builder = PromptBuilder()
        self.context_manager = ContextManager()
        self.history_mapper = HistoryMapper()

    def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationResponse:

        entity = self.repository.create(
            user_id=user_id,
            conversation=conversation,
        )

        return ConversationResponse.model_validate(entity)

    def send_message(
        self,
        conversation_id: UUID,
        content: str,
    ) -> MessageResponse:

        conversation = self.repository.get_by_id(conversation_id)

        if conversation is None:
            # TODO: Replace with BusinessException in Sprint 6
            raise ValueError("Conversation not found")

        history_entities = self.message_repository.list_by_conversation(conversation_id)

        history_messages = self.history_mapper.to_chat_messages(history_entities)

        history_messages = self.context_manager.build_history(history_messages)

        request = self.prompt_builder.build(
            system_prompt=conversation.system_prompt,
            history=history_messages,
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

        self.message_repository.create(user_message)

        assistant_response = self.provider.chat(request)

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

        self.message_repository.create(assistant_message)

        return MessageResponse.model_validate(assistant_message)

    def get_messages(
        self,
        conversation_id: UUID,
    ) -> list[MessageResponse]:

        entities = self.message_repository.list_by_conversation(conversation_id)

        return [MessageResponse.model_validate(entity) for entity in entities]
