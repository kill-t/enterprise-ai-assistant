from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    """创建会话时允许客户端提交的配置。"""

    title: str = Field(min_length=1, max_length=255)
    system_prompt: str | None = None
    model_name: str = "gpt-5"
    temperature: float = Field(default=0.7, ge=0, le=2)

# 模型名称将在多 Provider 网关稳定后改为受支持模型枚举。

class ConversationResponse(BaseModel):
    """返回给客户端的会话基础信息。"""

    id: UUID
    title: str
    model_name: str
    temperature: float
    total_tokens: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
