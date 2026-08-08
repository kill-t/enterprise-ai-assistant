from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base

if TYPE_CHECKING:
    from app.modules.knowledge.entity.document import DocumentEntity


class KnowledgeBaseEntity(Base):
    """保存用户私有知识库及其不可混用的 Embedding 配置快照。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "name",
            name="uq_knowledge_bases_user_name",
        ),
        CheckConstraint(
            "embedding_dimension = 1024",
            name="ck_knowledge_bases_embedding_dimension_1024",
        ),
        Index(
            "ix_knowledge_bases_user_archived",
            "user_id",
            "is_archived",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 向量列固定为 1024 维，因此知识库必须冻结创建时的 Provider 和模型，禁止混合索引。
    embedding_provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="mock",
        server_default=text("'mock'"),
    )
    embedding_model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="mock-hash-1024",
        server_default=text("'mock-hash-1024'"),
    )
    embedding_dimension: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1024,
        server_default=text("1024"),
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
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

    documents: Mapped[list["DocumentEntity"]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
