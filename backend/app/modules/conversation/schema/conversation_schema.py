from datetime import datetime
from uuid import UUID

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.llm.provider_factory import ProviderFactory


class ConversationCreate(BaseModel):
    """创建会话时允许客户端提交的配置。"""

    title: str = Field(min_length=1, max_length=255)
    system_prompt: str | None = None
    # 前端可以显式选择模型；未选择时必须跟随当前 Provider，不能保存过期的硬编码模型名。
    model_name: str = Field(
        default_factory=ProviderFactory.get_model_name,
        min_length=1,
        max_length=100,
    )
    temperature: float = Field(default=0.7, ge=0, le=2)


# 后续 Tool/Agent 引入多 Provider 注册表后，再由注册表校验客户端显式提交的模型名。


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
