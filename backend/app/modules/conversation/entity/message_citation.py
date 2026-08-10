"""定义助手消息引用的可追溯来源和不可变展示快照。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.modules.conversation.entity.message import MessageEntity
from app.modules.knowledge.entity.document import DocumentEntity
from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity


class MessageCitationEntity(Base):
    """保存一条回答当时使用的来源，以及来源变化后仍可展示的证据快照。"""

    __tablename__ = "message_citations"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "source_order",
            name="uq_message_citations_message_source_order",
        ),
        CheckConstraint(
            "source_order > 0",
            name="ck_message_citations_source_order_positive",
        ),
        CheckConstraint(
            "retrieval_score >= -1 AND retrieval_score <= 1",
            name="ck_message_citations_score_range",
        ),
        CheckConstraint(
            "chunk_index_snapshot >= 0",
            name="ck_message_citations_chunk_index_nonnegative",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_start > 0",
            name="ck_message_citations_page_start_positive",
        ),
        CheckConstraint(
            "page_end IS NULL OR page_end > 0",
            name="ck_message_citations_page_end_positive",
        ),
        CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_message_citations_page_order",
        ),
        CheckConstraint(
            "length(knowledge_base_name_snapshot) > 0",
            name="ck_message_citations_knowledge_base_name_nonempty",
        ),
        CheckConstraint(
            "length(filename_snapshot) > 0",
            name="ck_message_citations_filename_nonempty",
        ),
        CheckConstraint(
            "length(content_snapshot) > 0",
            name="ck_message_citations_content_nonempty",
        ),
        Index("ix_message_citations_knowledge_base_id", "knowledge_base_id"),
        Index("ix_message_citations_document_id", "document_id"),
        Index("ix_message_citations_chunk_id", "chunk_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    message_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 活引用用于当前数据关联；来源删除或重新入库时置空，历史展示依靠下方快照。
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        nullable=True,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )

    knowledge_base_name_snapshot: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    filename_snapshot: Mapped[str] = mapped_column(String(512), nullable=False)
    chunk_index_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path_snapshot: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    message: Mapped[MessageEntity] = relationship()
    knowledge_base: Mapped[KnowledgeBaseEntity | None] = relationship()
    document: Mapped[DocumentEntity | None] = relationship()
    chunk: Mapped[DocumentChunkEntity | None] = relationship()
