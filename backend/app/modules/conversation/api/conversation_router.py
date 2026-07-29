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
async def create_conversation(
    request: ConversationCreate,
    service: ConversationServiceDep,
):
    """为当前临时用户创建一个新会话。"""
    return await service.create(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        conversation=request,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
)
async def send_message(
    conversation_id: UUID,
    request: MessageCreate,
    service: ConversationServiceDep,
):
    """接收用户问题并等待会话服务生成助手消息。"""

    return await service.send_message(
        conversation_id=conversation_id,
        content=request.content,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def get_messages(
    conversation_id: UUID,
    service: ConversationServiceDep,
):
    """按时间顺序返回指定会话的完整消息列表。"""

    return await service.get_messages(conversation_id)


@router.post(
    "/{conversation_id}/messages/{message_id}/retry",
    response_model=MessageResponse,
)
async def retry_failed_message(
    conversation_id: UUID,
    message_id: UUID,
    service: ConversationServiceDep,
):
    """重试指定会话中失败的助手消息，并复用原消息记录。"""
    return await service.retry_failed_message(
        conversation_id=conversation_id,
        message_id=message_id,
    )
