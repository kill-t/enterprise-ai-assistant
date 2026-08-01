from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import MessageRole, MessageStatus


class MessageCreate(BaseModel):
    """用户发送消息时的请求结构。"""

    content: str


class MessageResponse(BaseModel):
    """包含生命周期状态和安全错误信息的消息响应。"""

    id: UUID

    role: MessageRole

    content: str

    model_name: str

    status: MessageStatus

    error_code: str | None

    error_message: str | None

    created_at: datetime

    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class MessageStreamEventType(str, Enum):
    """区分 Service 流的开始、正文增量、成功完成和失败终点。"""

    STARTED = "started"
    DELTA = "delta"
    COMPLETED = "completed"
    ERROR = "error"


class MessageStreamStartedEvent(BaseModel):
    """表示用户消息和助手占位已经成功提交到 PostgreSQL。"""

    type: Literal[MessageStreamEventType.STARTED] = MessageStreamEventType.STARTED
    user_message: MessageResponse
    assistant_message: MessageResponse


class MessageStreamDeltaEvent(BaseModel):
    """携带指定助手消息本次新增的非空正文片段。"""

    type: Literal[MessageStreamEventType.DELTA] = MessageStreamEventType.DELTA
    message_id: UUID
    delta: str = Field(min_length=1)


class MessageStreamCompletedEvent(BaseModel):
    """表示完整助手消息已经成功提交并完成缓存回填。"""

    type: Literal[MessageStreamEventType.COMPLETED] = MessageStreamEventType.COMPLETED
    message: MessageResponse


class MessageStreamErrorEvent(BaseModel):
    """表示助手消息已经以安全错误信息持久化为失败状态。"""

    type: Literal[MessageStreamEventType.ERROR] = MessageStreamEventType.ERROR
    message: MessageResponse


# 后续 SSE 层只负责序列化该联合类型，不再解释 Provider 厂商事件。
MessageStreamEvent: TypeAlias = Annotated[
    MessageStreamStartedEvent
    | MessageStreamDeltaEvent
    | MessageStreamCompletedEvent
    | MessageStreamErrorEvent,
    Field(discriminator="type"),
]
