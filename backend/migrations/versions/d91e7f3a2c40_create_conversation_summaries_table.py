"""创建会话滚动摘要表。

Revision ID: d91e7f3a2c40
Revises: a4d8f2c7e615
Create Date: 2026-07-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# Alembic 迁移版本及其依赖关系。
revision: str = "d91e7f3a2c40"
down_revision: Union[str, Sequence[str], None] = "a4d8f2c7e615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建一会话一摘要的持久化结构，并增加游标与数据约束。"""
    op.create_table(
        "conversation_summaries",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "covered_until_created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("covered_until_message_id", sa.UUID(), nullable=True),
        sa.Column(
            "token_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "token_count >= 0",
            name="ck_conversation_summaries_token_count_nonnegative",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_conversation_summaries_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["covered_until_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )


def downgrade() -> None:
    """删除会话滚动摘要表。"""
    op.drop_table("conversation_summaries")
