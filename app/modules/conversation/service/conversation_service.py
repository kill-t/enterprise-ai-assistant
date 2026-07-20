from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.conversation.repository.conversation_repository import (
    ConversationRepository,
)
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
)
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.modules.conversation.repository.message_repository import MessageRepository
from app.common.enums import MessageRole, MessageStatus
from app.modules.conversation.entity.message import MessageEntity
from app.infrastructure.llm.schemas import ChatMessage, ChatRequest, ChatRole


class ConversationService:

    def __init__(self, db: Session):
        self.repository = ConversationRepository(db)
        self.provider = ProviderFactory.create()
        self.message_repository = MessageRepository(db)
        self.db = db

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
    ) -> MessageEntity:
        user_message = MessageEntity(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            model_name="user",
            status=MessageStatus.COMPLETED,
        )

        self.message_repository.create(user_message)

        request = ChatRequest(
            messages=[
                ChatMessage(
                    role=ChatRole.USER,
                    content=content,
                )
            ]
        )

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

        return assistant_message
