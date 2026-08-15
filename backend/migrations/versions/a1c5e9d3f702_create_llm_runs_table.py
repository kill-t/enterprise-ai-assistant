"""创建不保存敏感正文的 LLM 调用审计表。

Revision ID: a1c5e9d3f702
Revises: f4c8a2d6e190
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1c5e9d3f702"
down_revision: Union[str, Sequence[str], None] = "f4c8a2d6e190"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建终态审计记录、非负约束和常用查询索引。"""
    llm_run_status = sa.Enum(
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        name="llm_run_status",
    )
    op.create_table(
        "llm_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("status", llm_run_status, nullable=False),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("finish_reason", sa.String(length=100), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0",
            name="ck_llm_runs_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name="ck_llm_runs_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "length(operation) > 0 AND length(provider) > 0 AND length(model) > 0",
            name="ck_llm_runs_names_nonempty",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_runs_user_id", "llm_runs", ["user_id"], unique=False)
    op.create_index(
        "ix_llm_runs_conversation_id",
        "llm_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_runs_message_id",
        "llm_runs",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_runs_operation_started_at",
        "llm_runs",
        ["operation", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_llm_runs_status_started_at",
        "llm_runs",
        ["status", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """先删除索引和表，再删除 PostgreSQL 审计状态枚举。"""
    op.drop_index("ix_llm_runs_status_started_at", table_name="llm_runs")
    op.drop_index("ix_llm_runs_operation_started_at", table_name="llm_runs")
    op.drop_index("ix_llm_runs_message_id", table_name="llm_runs")
    op.drop_index("ix_llm_runs_conversation_id", table_name="llm_runs")
    op.drop_index("ix_llm_runs_user_id", table_name="llm_runs")
    op.drop_table("llm_runs")
    sa.Enum(name="llm_run_status").drop(op.get_bind(), checkfirst=True)
