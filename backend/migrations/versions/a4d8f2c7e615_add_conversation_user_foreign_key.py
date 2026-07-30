"""为会话增加用户外键并回填历史所有者。

Revision ID: a4d8f2c7e615
Revises: 6f4c2a1b9d83
Create Date: 2026-07-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# Alembic 迁移版本及其依赖关系。
revision: str = "a4d8f2c7e615"
down_revision: Union[str, Sequence[str], None] = "6f4c2a1b9d83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为历史孤立会话创建停用用户，再添加不可绕过的所有权外键。"""
    op.execute(
        sa.text(
            """
            INSERT INTO users (id, email, password_hash, is_active)
            SELECT DISTINCT
                conversations.user_id,
                'legacy-' || conversations.user_id::text || '@local.invalid',
                '!legacy-migration-placeholder!',
                false
            FROM conversations
            LEFT JOIN users ON users.id = conversations.user_id
            WHERE users.id IS NULL
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    op.create_foreign_key(
        "fk_conversations_user_id_users",
        "conversations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """删除会话用户外键，并清理仍未启用的历史占位用户。"""
    op.drop_constraint(
        "fk_conversations_user_id_users",
        "conversations",
        type_="foreignkey",
    )
    op.execute(
        sa.text(
            """
            DELETE FROM users
            WHERE password_hash = '!legacy-migration-placeholder!'
              AND is_active = false
              AND email LIKE 'legacy-%@local.invalid'
            """
        )
    )
