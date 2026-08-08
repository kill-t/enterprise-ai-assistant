from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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

from app.common.enums import DocumentStatus
from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity
    from app.modules.knowledge.entity.document_ingestion_job import (
        DocumentIngestionJobEntity,
    )
    from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity


class DocumentEntity(Base):
    """保存原文件定位、去重哈希以及文档入库的当前业务状态。"""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_documents_knowledge_base_sha256",
        ),
        UniqueConstraint("object_key", name="uq_documents_object_key"),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_documents_size_bytes_positive",
        ),
        CheckConstraint(
            "page_count >= 0",
            name="ck_documents_page_count_nonnegative",
        ),
        CheckConstraint(
            "chunk_count >= 0",
            name="ck_documents_chunk_count_nonnegative",
        ),
        Index(
            "ix_documents_knowledge_base_status",
            "knowledge_base_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.PENDING,
        server_default=text("'PENDING'"),
    )
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    knowledge_base: Mapped["KnowledgeBaseEntity"] = relationship(
        back_populates="documents"
    )
    ingestion_jobs: Mapped[list["DocumentIngestionJobEntity"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chunks: Mapped[list["DocumentChunkEntity"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
