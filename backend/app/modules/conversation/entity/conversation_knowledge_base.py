"""定义会话与用户私有知识库之间的显式绑定实体。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity


class ConversationKnowledgeBaseEntity(Base):
    """记录某个会话允许检索的知识库，并天然阻止重复绑定。"""

    __tablename__ = "conversation_knowledge_bases"
    __table_args__ = (
        Index(
            "ix_conversation_knowledge_bases_knowledge_base_id",
            "knowledge_base_id",
        ),
    )

    # 两个外键共同构成业务唯一身份，比额外 UUID 更直接地表达“一次绑定”。
    conversation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    conversation: Mapped[ConversationEntity] = relationship()
    knowledge_base: Mapped[KnowledgeBaseEntity] = relationship()
