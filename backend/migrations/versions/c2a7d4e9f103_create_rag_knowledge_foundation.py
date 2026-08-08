"""创建 RAG 知识库、文档、入库任务和向量片段表。

Revision ID: c2a7d4e9f103
Revises: b9d5e7f2a410
Create Date: 2026-08-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR


revision: str = "c2a7d4e9f103"
down_revision: Union[str, Sequence[str], None] = "b9d5e7f2a410"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """启用 pgvector，并创建具有所有权、状态与追溯约束的 RAG 基础表。"""
    # 扩展由数据库级迁移负责启用，应用进程只注册客户端的 vector 编解码器。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    document_status = sa.Enum(
        "PENDING",
        "PROCESSING",
        "READY",
        "FAILED",
        "DELETED",
        name="document_status",
    )
    ingestion_job_status = sa.Enum(
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        name="document_ingestion_job_status",
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "embedding_provider",
            sa.String(length=50),
            server_default=sa.text("'mock'"),
            nullable=False,
        ),
        sa.Column(
            "embedding_model",
            sa.String(length=100),
            server_default=sa.text("'mock-hash-1024'"),
            nullable=False,
        ),
        sa.Column(
            "embedding_dimension",
            sa.Integer(),
            server_default=sa.text("1024"),
            nullable=False,
        ),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
            "embedding_dimension = 1024",
            name="ck_knowledge_bases_embedding_dimension_1024",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "name",
            name="uq_knowledge_bases_user_name",
        ),
    )
    op.create_index(
        "ix_knowledge_bases_user_archived",
        "knowledge_bases",
        ["user_id", "is_archived"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            document_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("parser_version", sa.String(length=100), nullable=True),
        sa.Column("chunker_version", sa.String(length=100), nullable=True),
        sa.Column(
            "page_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "chunk_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_documents_size_bytes_positive",
        ),
        sa.CheckConstraint(
            "page_count >= 0",
            name="ck_documents_page_count_nonnegative",
        ),
        sa.CheckConstraint(
            "chunk_count >= 0",
            name="ck_documents_chunk_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_documents_knowledge_base_sha256",
        ),
        sa.UniqueConstraint("object_key", name="uq_documents_object_key"),
    )
    op.create_index(
        "ix_documents_knowledge_base_status",
        "documents",
        ["knowledge_base_id", "status"],
        unique=False,
    )

    op.create_table(
        "document_ingestion_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            ingestion_job_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="ck_document_ingestion_jobs_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_document_ingestion_jobs_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_document_ingestion_jobs_idempotency_key",
        ),
    )
    op.create_index(
        "uq_document_ingestion_jobs_active_document",
        "document_ingestion_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_index(
        "ix_document_ingestion_jobs_retry_due",
        "document_ingestion_jobs",
        ["status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_document_ingestion_jobs_dispatch_claim",
        "document_ingestion_jobs",
        ["status", "dispatch_claimed_at"],
        unique=False,
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("heading_path", sa.String(length=1024), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        sa.CheckConstraint(
            "token_count > 0",
            name="ck_document_chunks_token_count_positive",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_start > 0",
            name="ck_document_chunks_page_start_positive",
        ),
        sa.CheckConstraint(
            "page_end IS NULL OR page_end > 0",
            name="ck_document_chunks_page_end_positive",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_document_chunks_page_order",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_document_chunks_document_id",
        "document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_embedding_hnsw_cosine",
        "document_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """删除 RAG 业务表和专用枚举，但保留可供其他模块复用的 vector 扩展。"""
    op.drop_index(
        "ix_document_chunks_embedding_hnsw_cosine",
        table_name="document_chunks",
        postgresql_using="hnsw",
    )
    op.drop_index(
        "ix_document_chunks_document_id",
        table_name="document_chunks",
    )
    op.drop_table("document_chunks")

    op.drop_index(
        "ix_document_ingestion_jobs_dispatch_claim",
        table_name="document_ingestion_jobs",
    )
    op.drop_index(
        "ix_document_ingestion_jobs_retry_due",
        table_name="document_ingestion_jobs",
    )
    op.drop_index(
        "uq_document_ingestion_jobs_active_document",
        table_name="document_ingestion_jobs",
    )
    op.drop_table("document_ingestion_jobs")
    op.drop_index(
        "ix_documents_knowledge_base_status",
        table_name="documents",
    )
    op.drop_table("documents")
    op.drop_index(
        "ix_knowledge_bases_user_archived",
        table_name="knowledge_bases",
    )
    op.drop_table("knowledge_bases")

    sa.Enum(name="document_ingestion_job_status").drop(
        op.get_bind(),
        checkfirst=True,
    )
    sa.Enum(name="document_status").drop(op.get_bind(), checkfirst=True)
