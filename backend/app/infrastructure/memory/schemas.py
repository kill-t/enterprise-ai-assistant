from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MemoryMessage(BaseModel):
    """跨业务层与缓存层传递的轻量消息结构，避免缓存依赖 ORM 实体。"""

    id: UUID | None = None
    role: str
    content: str
    created_at: datetime | None = None
