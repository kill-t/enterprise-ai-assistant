from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.common.dependencies import ConversationServiceDep
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.modules.conversation.schema.message_schema import (
    MessageCreate,
    MessageResponse,
)
from app.modules.identity.dependencies import CurrentUserDep


router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["会话"],
)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: ConversationCreate,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> ConversationResponse:
    """为当前登录用户创建新会话。"""
    return await service.create(
        user_id=current_user.id,
        conversation=request,
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
    include_archived: bool = Query(default=False),
) -> list[ConversationResponse]:
    """返回当前用户的会话列表，并可选择包含已归档会话。"""
    return await service.list_conversations(
        user_id=current_user.id,
        include_archived=include_archived,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> ConversationResponse:
    """返回属于当前用户的指定会话。"""
    return await service.get(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    request: ConversationUpdate,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> ConversationResponse:
    """修改当前用户会话的标题或归档状态。"""
    return await service.update(
        user_id=current_user.id,
        conversation_id=conversation_id,
        changes=request,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """删除当前用户的会话、消息和短期记忆。"""
    await service.delete(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: UUID,
    request: MessageCreate,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> MessageResponse:
    """校验会话所有权后接收用户问题并生成助手消息。"""
    return await service.send_message(
        user_id=current_user.id,
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
    current_user: CurrentUserDep,
) -> list[MessageResponse]:
    """校验会话所有权后按时间顺序返回完整消息列表。"""
    return await service.get_messages(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/retry",
    response_model=MessageResponse,
)
async def retry_failed_message(
    conversation_id: UUID,
    message_id: UUID,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> MessageResponse:
    """校验会话所有权后原位重试指定失败助手消息。"""
    return await service.retry_failed_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
