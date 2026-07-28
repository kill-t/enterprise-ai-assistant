from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.common.enums import MessageRole, MessageStatus


class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):

    id: UUID

    role: MessageRole

    content: str

    model_name: str

    status: MessageStatus

    error_code: str | None

    error_message: str | None

    created_at: datetime

    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
