from datetime import datetime
from uuid import UUID

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConversationCreate(BaseModel):
    """创建会话时允许客户端提交的配置。"""

    title: str = Field(min_length=1, max_length=255)
    system_prompt: str | None = None
    model_name: str = "gpt-5"
    temperature: float = Field(default=0.7, ge=0, le=2)


# 模型名称将在多 Provider 网关稳定后改为受支持模型枚举。


class ConversationUpdate(BaseModel):
    """允许用户修改的会话标题和归档状态。"""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    is_archived: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """拒绝空更新和显式 null，避免无意义事务或数据库非空错误。"""
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个更新字段")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title 不能为 null")
        if "is_archived" in self.model_fields_set and self.is_archived is None:
            raise ValueError("is_archived 不能为 null")
        return self


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
