from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.conversation.repository import ConversationRepository
from app.modules.conversation.schemas import (
    ConversationCreate,
    ConversationResponse,
)


class ConversationService:

    def __init__(self, db: Session):
        self.repository = ConversationRepository(db)

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