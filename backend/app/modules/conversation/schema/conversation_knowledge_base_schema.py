"""会话知识库选择的请求和响应契约。"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.modules.knowledge.schema.knowledge_base_schema import KnowledgeBaseResponse


class ConversationKnowledgeBaseUpdate(BaseModel):
    """表示用户希望原子替换成的新知识库选择，空列表表示全部解绑。"""

    knowledge_base_ids: list[UUID] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        """拒绝重复 ID，避免客户端误以为同一知识库会被检索多次。"""
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("knowledge_base_ids 不能重复")
        return self


class ConversationKnowledgeBaseSelectionResponse(BaseModel):
    """返回会话当前选择及每个知识库的可展示状态。"""

    conversation_id: UUID
    knowledge_bases: list[KnowledgeBaseResponse]
