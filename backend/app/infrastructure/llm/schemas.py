from enum import Enum
from pydantic import BaseModel


class ChatRole(str, Enum):

    SYSTEM = "system"

    USER = "user"

    ASSISTANT = "assistant"

    TOOL = "tool"


class ChatMessage(BaseModel):

    role: ChatRole

    content: str


class ChatRequest(BaseModel):

    messages: list[ChatMessage]

    temperature: float = 0.7

    stream: bool = False

    model: str | None = None

    metadata: dict[str, str] = {}


class ChatResponse(BaseModel):

    content: str

    model: str

    prompt_tokens: int = 0

    completion_tokens: int = 0

    total_tokens: int = 0

    finish_reason: str = "stop"
