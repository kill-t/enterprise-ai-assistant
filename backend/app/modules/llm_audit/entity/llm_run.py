"""定义不保存请求与响应正文的 LLM 调用审计表。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.enums import LLMRunStatus
from app.infrastructure.database.base import Base

# 审计 Recorder 可能由独立 Worker 直接导入；显式注册外键目标，不能依赖 FastAPI 导入顺序。
from app.modules.conversation.entity.conversation import (  # noqa: F401
    ConversationEntity,
)
from app.modules.conversation.entity.message import MessageEntity  # noqa: F401
from app.modules.identity.entity.user import UserEntity  # noqa: F401


class LLMRunEntity(Base):
    """保存模型、用量、耗时、终态和安全关联 ID。"""

    __tablename__ = "llm_runs"
    __table_args__ = (
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_llm_runs_tokens_nonnegative",
        ),
        CheckConstraint(
            "latency_ms >= 0",
            name="ck_llm_runs_latency_nonnegative",
        ),
        CheckConstraint(
            "length(operation) > 0 AND length(provider) > 0 AND length(model) > 0",
            name="ck_llm_runs_names_nonempty",
        ),
        Index("ix_llm_runs_operation_started_at", "operation", "started_at"),
        Index("ix_llm_runs_status_started_at", "status", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    # HTTP 请求可通过该字段与结构化日志关联；Worker 没有请求上下文时允许为空。
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[LLMRunStatus] = mapped_column(
        Enum(LLMRunStatus, name="llm_run_status"),
        nullable=False,
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 只记录异常类名，例如 TimeoutError；绝不保存可能包含密钥的异常正文。
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
