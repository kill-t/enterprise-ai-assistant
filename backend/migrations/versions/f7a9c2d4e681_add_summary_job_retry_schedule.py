"""增加摘要任务延迟重试时间。

Revision ID: f7a9c2d4e681
Revises: e3b1c8a46f72
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7a9c2d4e681"
down_revision: Union[str, Sequence[str], None] = "e3b1c8a46f72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加下一次重试时间，并建立到期任务扫描索引。"""
    op.add_column(
        "conversation_summary_jobs",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversation_summary_jobs_retry_due",
        "conversation_summary_jobs",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除摘要任务到期扫描索引和延迟重试时间。"""
    op.drop_index(
        "ix_conversation_summary_jobs_retry_due",
        table_name="conversation_summary_jobs",
    )
    op.drop_column("conversation_summary_jobs", "next_retry_at")
