from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import ConversationServiceDep
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.modules.conversation.api.sse import (
    close_message_stream,
    iter_sse_frames,
)
from app.modules.conversation.dependencies import (
    ConversationKnowledgeBaseServiceDep,
)
from app.modules.conversation.schema.conversation_knowledge_base_schema import (
    ConversationKnowledgeBaseSelectionResponse,
    ConversationKnowledgeBaseUpdate,
)
from app.modules.conversation.schema.conversation_schema import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.modules.conversation.schema.message_schema import (
    MessageCreate,
    MessageHistoryResponse,
    MessageResponse,
    MessageStreamStartedEvent,
)
from app.modules.identity.dependencies import CurrentUserDep


router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["会话"],
)


@router.post(
    "", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
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


@router.get(
    "/{conversation_id}/knowledge-bases",
    response_model=ConversationKnowledgeBaseSelectionResponse,
)
async def get_conversation_knowledge_bases(
    conversation_id: UUID,
    service: ConversationKnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> ConversationKnowledgeBaseSelectionResponse:
    """返回当前用户会话已选择的知识库及其最新状态。"""
    return await service.get_selection(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.put(
    "/{conversation_id}/knowledge-bases",
    response_model=ConversationKnowledgeBaseSelectionResponse,
)
async def replace_conversation_knowledge_bases(
    conversation_id: UUID,
    request: ConversationKnowledgeBaseUpdate,
    service: ConversationKnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> ConversationKnowledgeBaseSelectionResponse:
    """原子替换当前用户会话的完整知识库选择。"""
    return await service.replace_selection(
        user_id=current_user.id,
        conversation_id=conversation_id,
        request=request,
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


@router.post(
    "/{conversation_id}/messages/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "按 started、可选 sources、delta、completed 或 error 顺序返回消息流",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def stream_message(
    conversation_id: UUID,
    request: MessageCreate,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    """预取持久化成功的 started 后，再建立 SSE 流式响应。"""
    message_stream = service.stream_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        content=request.content,
    )
    try:
        # 预取发生在响应头发送前，让所有权、预算和数据库错误仍能返回正确 HTTP 状态。
        first_event = await anext(message_stream)
    except StopAsyncIteration as exc:
        await close_message_stream(message_stream)
        raise BusinessException(
            code=ErrorCode.INTERNAL_ERROR,
            message="消息流启动失败",
            status_code=500,
        ) from exc
    except BaseException:
        # 同时覆盖业务异常和任务取消；清理后原样抛出，交给上层决定 HTTP 结果。
        await close_message_stream(message_stream)
        raise

    if not isinstance(first_event, MessageStreamStartedEvent):
        await close_message_stream(message_stream)
        raise BusinessException(
            code=ErrorCode.INTERNAL_ERROR,
            message="消息流启动失败",
            status_code=500,
        )

    return StreamingResponse(
        iter_sse_frames(first_event, message_stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 禁止 Nginx 代理缓冲，否则前端可能在结束时才一次收到全部片段。
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageHistoryResponse],
)
async def get_messages(
    conversation_id: UUID,
    service: ConversationServiceDep,
    current_user: CurrentUserDep,
) -> list[MessageHistoryResponse]:
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
    """校验会话所有权后原位重试指定失败或已取消的助手消息。"""
    return await service.retry_failed_message(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
