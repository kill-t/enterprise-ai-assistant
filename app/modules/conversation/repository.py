from uuid import UUID

from sqlalchemy.orm import Session

from app.infrastructure.database.entities.conversation import ConversationEntity
from app.modules.conversation.schemas import ConversationCreate


class ConversationRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        user_id: UUID,
        conversation: ConversationCreate,
    ) -> ConversationEntity:

        entity = ConversationEntity(
            user_id=user_id,
            title=conversation.title,
            system_prompt=conversation.system_prompt,
            model_name=conversation.model_name,
            temperature=conversation.temperature,
        )

        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)

        return entity