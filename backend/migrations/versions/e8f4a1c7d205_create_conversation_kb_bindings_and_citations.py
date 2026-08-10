"""创建会话知识库绑定与消息引用快照表。

Revision ID: e8f4a1c7d205
Revises: c2a7d4e9f103
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e8f4a1c7d205"
down_revision: Union[str, Sequence[str], None] = "c2a7d4e9f103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建显式绑定和可在来源变化后继续审计的引用快照。"""
    op.create_table(
        "conversation_knowledge_bases",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "knowledge_base_id"),
    )
    op.create_index(
        "ix_conversation_knowledge_bases_knowledge_base_id",
        "conversation_knowledge_bases",
        ["knowledge_base_id"],
        unique=False,
    )

    op.create_table(
        "message_citations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=True),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column(
            "knowledge_base_name_snapshot",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("filename_snapshot", sa.String(length=512), nullable=False),
        sa.Column("chunk_index_snapshot", sa.Integer(), nullable=False),
        sa.Column("content_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "heading_path_snapshot",
            sa.String(length=1024),
            nullable=True,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_order > 0",
            name="ck_message_citations_source_order_positive",
        ),
        sa.CheckConstraint(
            "retrieval_score >= -1 AND retrieval_score <= 1",
            name="ck_message_citations_score_range",
        ),
        sa.CheckConstraint(
            "chunk_index_snapshot >= 0",
            name="ck_message_citations_chunk_index_nonnegative",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_start > 0",
            name="ck_message_citations_page_start_positive",
        ),
        sa.CheckConstraint(
            "page_end IS NULL OR page_end > 0",
            name="ck_message_citations_page_end_positive",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_message_citations_page_order",
        ),
        sa.CheckConstraint(
            "length(knowledge_base_name_snapshot) > 0",
            name="ck_message_citations_knowledge_base_name_nonempty",
        ),
        sa.CheckConstraint(
            "length(filename_snapshot) > 0",
            name="ck_message_citations_filename_nonempty",
        ),
        sa.CheckConstraint(
            "length(content_snapshot) > 0",
            name="ck_message_citations_content_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "source_order",
            name="uq_message_citations_message_source_order",
        ),
    )
    op.create_index(
        "ix_message_citations_knowledge_base_id",
        "message_citations",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_citations_document_id",
        "message_citations",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_citations_chunk_id",
        "message_citations",
        ["chunk_id"],
        unique=False,
    )


def downgrade() -> None:
    """先删除依赖消息和知识文档的引用，再删除会话知识库绑定。"""
    op.drop_index(
        "ix_message_citations_chunk_id",
        table_name="message_citations",
    )
    op.drop_index(
        "ix_message_citations_document_id",
        table_name="message_citations",
    )
    op.drop_index(
        "ix_message_citations_knowledge_base_id",
        table_name="message_citations",
    )
    op.drop_table("message_citations")

    op.drop_index(
        "ix_conversation_knowledge_bases_knowledge_base_id",
        table_name="conversation_knowledge_bases",
    )
    op.drop_table("conversation_knowledge_bases")
