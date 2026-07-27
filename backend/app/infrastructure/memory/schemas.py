from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MemoryMessage(BaseModel):
    """
    Memory中的消息结构

    不直接使用MessageEntity
    """

    id: UUID | None = None

    role: str

    content: str

    created_at: datetime | None = None
