from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field


class ChatRole(str, Enum):
    """发送给模型的标准消息角色。"""

    SYSTEM = "system"

    USER = "user"

    ASSISTANT = "assistant"

    TOOL = "tool"


class ChatResponseFormat(str, Enum):
    """约束 Provider 返回普通文本或合法 JSON 对象。"""

    TEXT = "text"
    JSON_OBJECT = "json_object"


class ChatStreamEventType(str, Enum):
    """区分流式正文增量、完整成功和调用失败。"""

    DELTA = "delta"
    COMPLETED = "completed"
    ERROR = "error"


class ChatMessage(BaseModel):
    """发送给模型的单条标准消息。"""

    role: ChatRole

    content: str


class ChatRequest(BaseModel):
    """Provider 接收的统一聊天请求。"""

    messages: list[ChatMessage]

    temperature: float = 0.7

    stream: bool = False

    model: str | None = None

    response_format: ChatResponseFormat = ChatResponseFormat.TEXT

    metadata: dict[str, str] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Provider 返回的统一聊天结果与 token 用量。"""

    content: str

    model: str

    prompt_tokens: int = 0

    completion_tokens: int = 0

    total_tokens: int = 0

    finish_reason: str = "stop"


class ChatStreamDeltaEvent(BaseModel):
    """携带本次新产生的非空文本片段。"""

    type: Literal[ChatStreamEventType.DELTA] = ChatStreamEventType.DELTA
    delta: str = Field(min_length=1)


class ChatStreamCompletedEvent(BaseModel):
    """表示流正常结束，并携带拼接后的完整响应和 token 用量。"""

    type: Literal[ChatStreamEventType.COMPLETED] = ChatStreamEventType.COMPLETED
    response: ChatResponse


class ChatStreamErrorEvent(BaseModel):
    """表示流式调用失败，只向上层暴露稳定错误信息。"""

    type: Literal[ChatStreamEventType.ERROR] = ChatStreamEventType.ERROR
    error_code: str = Field(min_length=1)
    error_message: str = Field(min_length=1)
    model: str | None = None


# 使用可辨识联合类型，让后续 Service 和 SSE 层可以根据 type 安全缩窄事件结构。
ChatStreamEvent: TypeAlias = Annotated[
    ChatStreamDeltaEvent | ChatStreamCompletedEvent | ChatStreamErrorEvent,
    Field(discriminator="type"),
]
