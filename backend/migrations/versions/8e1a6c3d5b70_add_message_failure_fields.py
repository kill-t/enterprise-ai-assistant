"""为消息增加模型失败信息

Revision ID: 8e1a6c3d5b70
Revises: 279b38fa70fc
Create Date: 2026-07-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8e1a6c3d5b70"
down_revision: Union[str, Sequence[str], None] = "279b38fa70fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "finished_at")
    op.drop_column("messages", "error_message")
    op.drop_column("messages", "error_code")
