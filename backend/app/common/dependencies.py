from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.infrastructure.database.session import get_db
from app.modules.conversation.service.conversation_service import ConversationService


def get_conversation_service(
    db: Session = Depends(get_db),
) -> ConversationService:
    return ConversationService(db)


ConversationServiceDep = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]