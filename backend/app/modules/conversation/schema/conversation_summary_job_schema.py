from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import SummaryJobStatus


class ConversationSummaryJobCreate(BaseModel):
    """创建摘要任务时需要冻结的目标边界和重试上限。"""

    conversation_id: UUID
    target_message_id: UUID
    target_created_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=255)
    max_attempts: int = Field(default=3, gt=0)


class ConversationSummaryJobResponse(BaseModel):
    """展示摘要任务持久化状态、重试信息和时间边界。"""

    id: UUID
    conversation_id: UUID
    target_message_id: UUID | None
    target_created_at: datetime
    idempotency_key: str
    celery_task_id: str | None
    status: SummaryJobStatus
    attempts: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
