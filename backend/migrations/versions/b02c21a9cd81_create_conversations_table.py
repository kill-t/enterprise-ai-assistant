"""创建会话表。

Revision ID: b02c21a9cd81
Revises: 
Create Date: 2026-07-18 19:12:04.423921

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Alembic 迁移版本及其依赖关系。
revision: str = 'b02c21a9cd81'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建保存会话配置与统计信息的 conversations 表。"""
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('system_prompt', sa.Text(), nullable=True),
    sa.Column('model_name', sa.String(length=100), nullable=False),
    sa.Column('temperature', sa.Float(), nullable=False),
    sa.Column('total_tokens', sa.Integer(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """删除 conversations 表，撤销本次迁移。"""
    op.drop_table('conversations')
