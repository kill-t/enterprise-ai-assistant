from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _strip_name(value: object) -> object:
    """在长度校验前去掉名称两端空格。"""
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_description(value: object) -> object:
    """清理描述两端空格，并把纯空格描述转换为数据库空值。"""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


class KnowledgeBaseCreate(BaseModel):
    """创建用户私有知识库时允许客户端提交的字段。"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)

    normalize_name = field_validator("name", mode="before")(_strip_name)
    normalize_description = field_validator("description", mode="before")(
        _normalize_description
    )


class KnowledgeBaseUpdate(BaseModel):
    """修改知识库名称、描述或归档状态时允许提交的字段。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    is_archived: bool | None = None

    normalize_name = field_validator("name", mode="before")(_strip_name)
    normalize_description = field_validator("description", mode="before")(
        _normalize_description
    )

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """拒绝空更新，并区分“没传描述”和“传 null 清空描述”。"""
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个更新字段")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name 不能为 null")
        if "is_archived" in self.model_fields_set and self.is_archived is None:
            raise ValueError("is_archived 不能为 null")
        return self


class KnowledgeBaseResponse(BaseModel):
    """返回给客户端的知识库信息和不可变 Embedding 配置快照。"""

    id: UUID
    name: str
    description: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
