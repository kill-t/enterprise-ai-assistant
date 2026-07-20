from uuid import UUID

from fastapi import APIRouter

from app.common.dependencies import ConversationServiceDep
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
)

router = APIRouter(
    prefix="/conversations",
    tags=["Conversation"],
)


@router.post(
    "",
    response_model=ConversationResponse,
)
def create_conversation(
    request: ConversationCreate,
    service: ConversationServiceDep,
):
    return service.create(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        conversation=request,
    )