"""为 Chunk 和消息引用增加可扩展的结构化来源位置。

Revision ID: d4e8b6c1a209
Revises: a1c5e9d3f702
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4e8b6c1a209"
down_revision: Union[str, Sequence[str], None] = "a1c5e9d3f702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """先添加可空列并回填旧 PDF/TXT/Markdown 数据，再收紧非空约束。"""
    jsonb_type = postgresql.JSONB(astext_type=sa.Text())
    op.add_column(
        "document_chunks",
        sa.Column("source_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("source_locator", jsonb_type, nullable=True),
    )
    op.add_column(
        "message_citations",
        sa.Column("source_type_snapshot", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "message_citations",
        sa.Column(
            "source_locator_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 旧数据只有页码和标题字符串；迁移时转成与新 Pydantic 联合类型相同的 JSON 形状。
    op.execute(
        r"""
        UPDATE document_chunks AS chunk
        SET
            source_type = CASE
                WHEN lower(document.filename) LIKE '%.pdf' THEN 'pdf'
                WHEN lower(document.filename) LIKE '%.md'
                  OR lower(document.filename) LIKE '%.markdown' THEN 'markdown'
                ELSE 'text'
            END,
            source_locator = CASE
                WHEN chunk.page_start IS NOT NULL THEN jsonb_strip_nulls(
                    jsonb_build_object(
                        'kind', 'page',
                        'page_start', chunk.page_start,
                        'page_end', COALESCE(chunk.page_end, chunk.page_start),
                        'heading_path', CASE
                            WHEN chunk.heading_path IS NULL THEN NULL
                            ELSE to_jsonb(
                                regexp_split_to_array(
                                    chunk.heading_path,
                                    '[[:space:]]*>[[:space:]]*'
                                )
                            )
                        END
                    )
                )
                WHEN chunk.heading_path IS NOT NULL THEN jsonb_build_object(
                    'kind', 'section',
                    'heading_path', to_jsonb(
                        regexp_split_to_array(
                            chunk.heading_path,
                            '[[:space:]]*>[[:space:]]*'
                        )
                    )
                )
                ELSE '{"kind": "document"}'::jsonb
            END
        FROM documents AS document
        WHERE document.id = chunk.document_id
        """
    )
    op.execute(
        r"""
        UPDATE message_citations AS citation
        SET
            source_type_snapshot = CASE
                WHEN lower(citation.filename_snapshot) LIKE '%.pdf' THEN 'pdf'
                WHEN lower(citation.filename_snapshot) LIKE '%.md'
                  OR lower(citation.filename_snapshot) LIKE '%.markdown' THEN 'markdown'
                ELSE 'text'
            END,
            source_locator_snapshot = CASE
                WHEN citation.page_start IS NOT NULL THEN jsonb_strip_nulls(
                    jsonb_build_object(
                        'kind', 'page',
                        'page_start', citation.page_start,
                        'page_end', COALESCE(citation.page_end, citation.page_start),
                        'heading_path', CASE
                            WHEN citation.heading_path_snapshot IS NULL THEN NULL
                            ELSE to_jsonb(
                                regexp_split_to_array(
                                    citation.heading_path_snapshot,
                                    '[[:space:]]*>[[:space:]]*'
                                )
                            )
                        END
                    )
                )
                WHEN citation.heading_path_snapshot IS NOT NULL THEN jsonb_build_object(
                    'kind', 'section',
                    'heading_path', to_jsonb(
                        regexp_split_to_array(
                            citation.heading_path_snapshot,
                            '[[:space:]]*>[[:space:]]*'
                        )
                    )
                )
                ELSE '{"kind": "document"}'::jsonb
            END
        """
    )

    op.alter_column("document_chunks", "source_type", nullable=False)
    op.alter_column("document_chunks", "source_locator", nullable=False)
    op.alter_column("message_citations", "source_type_snapshot", nullable=False)
    op.alter_column(
        "message_citations",
        "source_locator_snapshot",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_document_chunks_source_type_nonempty",
        "document_chunks",
        "length(source_type) > 0",
    )
    op.create_check_constraint(
        "ck_document_chunks_source_locator_object",
        "document_chunks",
        "jsonb_typeof(source_locator) = 'object' AND source_locator ? 'kind'",
    )
    op.create_check_constraint(
        "ck_message_citations_source_type_nonempty",
        "message_citations",
        "length(source_type_snapshot) > 0",
    )
    op.create_check_constraint(
        "ck_message_citations_source_locator_object",
        "message_citations",
        "jsonb_typeof(source_locator_snapshot) = 'object' "
        "AND source_locator_snapshot ? 'kind'",
    )


def downgrade() -> None:
    """删除新增约束和位置列，旧页码及标题字段继续保留。"""
    op.drop_constraint(
        "ck_message_citations_source_locator_object",
        "message_citations",
        type_="check",
    )
    op.drop_constraint(
        "ck_message_citations_source_type_nonempty",
        "message_citations",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_chunks_source_locator_object",
        "document_chunks",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_chunks_source_type_nonempty",
        "document_chunks",
        type_="check",
    )
    op.drop_column("message_citations", "source_locator_snapshot")
    op.drop_column("message_citations", "source_type_snapshot")
    op.drop_column("document_chunks", "source_locator")
    op.drop_column("document_chunks", "source_type")
