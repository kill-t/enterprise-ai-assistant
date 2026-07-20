from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.common.enums import MessageRole


class MessageResponse(BaseModel):

    id: UUID

    role: MessageRole

    content: str

    model_name: str

    created_at: datetime


    model_config = ConfigDict(
        from_attributes=True
    )