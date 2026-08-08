from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.enums import DocumentIngestionJobStatus
from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.modules.knowledge.entity.document import DocumentEntity


class DocumentIngestionJobEntity(Base):
    """保存可重试、可恢复且具有重复执行保护的文档入库任务。"""

    __tablename__ = "document_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_document_ingestion_jobs_idempotency_key",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_document_ingestion_jobs_attempts_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_document_ingestion_jobs_max_attempts_positive",
        ),
        Index(
            "uq_document_ingestion_jobs_active_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
        Index(
            "ix_document_ingestion_jobs_retry_due",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_document_ingestion_jobs_dispatch_claim",
            "status",
            "dispatch_claimed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[DocumentIngestionJobStatus] = mapped_column(
        Enum(DocumentIngestionJobStatus, name="document_ingestion_job_status"),
        nullable=False,
        default=DocumentIngestionJobStatus.PENDING,
        server_default=text("'PENDING'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    document: Mapped["DocumentEntity"] = relationship(
        back_populates="ingestion_jobs"
    )
