from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    system_prompt: str | None = None
    model_name: str = "gpt-5"
    temperature: float = Field(default=0.7, ge=0, le=2)
# TODO:model_name改为枚举类型切换模型

class ConversationResponse(BaseModel):
    id: UUID
    title: str
    model_name: str
    temperature: float
    total_tokens: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)