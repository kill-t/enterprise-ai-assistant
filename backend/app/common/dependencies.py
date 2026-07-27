from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.infrastructure.database.session import get_db
from app.infrastructure.memory.memory_manager import MemoryManager
from app.infrastructure.memory.provider import get_memory_manager
from app.modules.conversation.service.conversation_service import ConversationService


def get_conversation_service(
    db: Session = Depends(get_db),
    memory_manager: MemoryManager = Depends(get_memory_manager),
) -> ConversationService:
    return ConversationService(db, memory_manager)


ConversationServiceDep = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]
