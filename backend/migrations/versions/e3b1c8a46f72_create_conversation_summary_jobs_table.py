"""创建会话摘要后台任务表。

Revision ID: e3b1c8a46f72
Revises: d91e7f3a2c40
Create Date: 2026-07-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# Alembic 迁移版本及其依赖关系。
revision: str = "e3b1c8a46f72"
down_revision: Union[str, Sequence[str], None] = "d91e7f3a2c40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建可恢复摘要任务表、状态枚举、幂等约束和活动任务索引。"""
    summary_job_status = sa.Enum(
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        name="summary_job_status",
    )

    op.create_table(
        "conversation_summary_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("target_message_id", sa.UUID(), nullable=True),
        sa.Column("target_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            summary_job_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempts >= 0",
            name="ck_conversation_summary_jobs_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_conversation_summary_jobs_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_conversation_summary_jobs_idempotency_key",
        ),
    )
    op.create_index(
        "uq_conversation_summary_jobs_active_conversation",
        "conversation_summary_jobs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    """删除摘要任务表及其 PostgreSQL 状态枚举。"""
    op.drop_index(
        "uq_conversation_summary_jobs_active_conversation",
        table_name="conversation_summary_jobs",
    )
    op.drop_table("conversation_summary_jobs")
    sa.Enum(name="summary_job_status").drop(op.get_bind(), checkfirst=True)
