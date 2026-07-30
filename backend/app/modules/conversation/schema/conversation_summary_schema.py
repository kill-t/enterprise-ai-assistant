from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationSummaryContent(BaseModel):
    """约束滚动摘要必须包含的五类长期有效会话信息。"""

    user_goal_and_context: str = Field(min_length=1)
    confirmed_facts_and_decisions: str = Field(min_length=1)
    completed_items: str = Field(min_length=1)
    open_questions_and_todos: str = Field(min_length=1)
    useful_preferences: str = Field(min_length=1)

    @field_validator("*")
    @classmethod
    def strip_and_reject_blank_section(cls, value: str) -> str:
        """去除栏目首尾空白，并拒绝只包含空白字符的摘要内容。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("摘要栏目不能为空")
        return stripped

    def to_prompt_text(self) -> str:
        """按固定中文栏目渲染为可直接放入模型上下文的 Markdown。"""
        sections = (
            ("用户目标与背景", self.user_goal_and_context),
            ("已确认的事实、约束和决定", self.confirmed_facts_and_decisions),
            ("已完成事项", self.completed_items),
            ("未解决问题与待办", self.open_questions_and_todos),
            ("对后续回答有价值的偏好", self.useful_preferences),
        )
        return "\n\n".join(
            f"## {title}\n{content}" for title, content in sections
        )


class ConversationSummaryResponse(BaseModel):
    """提供摘要正文、覆盖游标和版本信息的内部响应结构。"""

    conversation_id: UUID
    content: str
    covered_until_created_at: datetime
    covered_until_message_id: UUID | None
    token_count: int
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
