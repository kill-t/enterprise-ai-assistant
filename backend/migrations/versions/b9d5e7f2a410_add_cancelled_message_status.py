"""增加消息取消终态。

Revision ID: b9d5e7f2a410
Revises: a8c4e6f1b209
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b9d5e7f2a410"
down_revision: Union[str, Sequence[str], None] = "a8c4e6f1b209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """允许消息从 PENDING 进入独立的 CANCELLED 终态。"""
    op.execute("ALTER TYPE messagestatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    """重建旧枚举，并把取消记录兼容映射为 FAILED。"""
    # PostgreSQL 不支持直接删除 enum 值，因此需要用旧值集合重建类型。
    op.execute(
        "CREATE TYPE messagestatus_without_cancelled "
        "AS ENUM ('PENDING', 'COMPLETED', 'FAILED')"
    )
    op.execute(
        """
        ALTER TABLE messages
        ALTER COLUMN status TYPE messagestatus_without_cancelled
        USING (
            CASE
                WHEN status::text = 'CANCELLED' THEN 'FAILED'
                ELSE status::text
            END
        )::messagestatus_without_cancelled
        """
    )
    op.execute("DROP TYPE messagestatus")
    op.execute("ALTER TYPE messagestatus_without_cancelled RENAME TO messagestatus")
