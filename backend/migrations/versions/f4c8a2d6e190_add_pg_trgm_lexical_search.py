"""增加 pg_trgm 中文关键词候选检索索引。

Revision ID: f4c8a2d6e190
Revises: e8f4a1c7d205
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f4c8a2d6e190"
down_revision: Union[str, Sequence[str], None] = "e8f4a1c7d205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """安装可信扩展，并为 Chunk 正文建立支持距离 Top-K 的 GiST 索引。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_document_chunks_content_gist_trgm",
        "document_chunks",
        ["content"],
        unique=False,
        postgresql_using="gist",
        postgresql_ops={"content": "gist_trgm_ops"},
    )


def downgrade() -> None:
    """只删除本模块索引，保留可能被其他表复用的 pg_trgm 扩展。"""
    op.drop_index(
        "ix_document_chunks_content_gist_trgm",
        table_name="document_chunks",
        postgresql_using="gist",
    )
