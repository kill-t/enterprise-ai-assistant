"""定义 Provider 装饰器与持久化 Recorder 之间的安全审计契约。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.common.enums import LLMRunStatus


@dataclass(frozen=True, slots=True)
class LLMRunRecord:
    """保存单次调用的非敏感终态，不提供 Prompt 或回答正文字段。"""

    request_id: str | None
    user_id: UUID | None
    conversation_id: UUID | None
    message_id: UUID | None
    operation: str
    provider: str
    model: str
    status: LLMRunStatus
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    finish_reason: str | None
    error_code: str | None
    error_type: str | None
    started_at: datetime
    finished_at: datetime


class LLMRunRecorder(Protocol):
    """允许装饰器把审计记录写入数据库或测试内存实现。"""

    async def record(self, record: LLMRunRecord) -> None:
        """持久化一条已经结束的模型调用记录。"""
        ...
