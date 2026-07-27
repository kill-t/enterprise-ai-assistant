from uuid import UUID

from fastapi import APIRouter

from app.common.dependencies import ConversationServiceDep
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
)
from app.modules.conversation.schema.message_schema import (
    MessageCreate,
    MessageResponse,
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


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
)
def send_message(
    conversation_id: UUID,
    request: MessageCreate,
    service: ConversationServiceDep,
):

    return service.send_message(
        conversation_id=conversation_id,
        content=request.content,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
def get_messages(
    conversation_id: UUID,
    service: ConversationServiceDep,
):

    return service.get_messages(conversation_id)
