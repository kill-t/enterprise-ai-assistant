from enum import Enum
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
