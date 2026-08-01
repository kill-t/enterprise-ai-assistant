"""增加摘要任务投递租约。

Revision ID: a8c4e6f1b209
Revises: f7a9c2d4e681
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a8c4e6f1b209"
down_revision: Union[str, Sequence[str], None] = "f7a9c2d4e681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加发布者投递租约时间和并发扫描索引。"""
    op.add_column(
        "conversation_summary_jobs",
        sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversation_summary_jobs_dispatch_claim",
        "conversation_summary_jobs",
        ["status", "dispatch_claimed_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除投递租约索引和时间字段。"""
    op.drop_index(
        "ix_conversation_summary_jobs_dispatch_claim",
        table_name="conversation_summary_jobs",
    )
    op.drop_column("conversation_summary_jobs", "dispatch_claimed_at")
