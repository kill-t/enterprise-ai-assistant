from pathlib import Path


def test_knowledge_entity_relationships_can_be_configured() -> None:
    """集中导入全部知识实体后，SQLAlchemy 必须能够解析双向关系。"""
    from sqlalchemy.orm import configure_mappers

    from app.modules.knowledge import models

    assert models.KnowledgeBaseEntity.__tablename__ == "knowledge_bases"
    configure_mappers()


def test_knowledge_base_belongs_to_user_and_freezes_embedding_config() -> None:
    """知识库必须归属于用户，并记录创建时使用的向量配置。"""
    from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity

    table = KnowledgeBaseEntity.__table__

    assert table.name == "knowledge_bases"
    assert any(
        foreign_key.target_fullname == "users.id"
        for foreign_key in table.c.user_id.foreign_keys
    )
    assert table.c.embedding_provider.nullable is False
    assert table.c.embedding_model.nullable is False
    assert table.c.embedding_dimension.nullable is False
    assert any(
        constraint.name == "ck_knowledge_bases_embedding_dimension_1024"
        for constraint in table.constraints
    )


def test_document_tracks_source_object_and_ingestion_state() -> None:
    """文档必须保存原文件定位、哈希、状态和可安全展示的错误。"""
    from app.modules.knowledge.entity.document import DocumentEntity

    table = DocumentEntity.__table__

    assert table.name == "documents"
    assert any(
        foreign_key.target_fullname == "knowledge_bases.id"
        for foreign_key in table.c.knowledge_base_id.foreign_keys
    )
    assert table.c.object_key.nullable is False
    assert table.c.sha256.nullable is False
    assert table.c.status.nullable is False
    assert table.c.error_code.nullable is True
    assert table.c.error_message.nullable is True
    assert any(
        constraint.name == "uq_documents_knowledge_base_sha256"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_documents_size_bytes_positive"
        for constraint in table.constraints
    )


def test_only_one_active_ingestion_job_is_allowed_per_document() -> None:
    """同一文档只能存在一个待处理或运行中的入库任务。"""
    from app.modules.knowledge.entity.document_ingestion_job import (
        DocumentIngestionJobEntity,
    )

    table = DocumentIngestionJobEntity.__table__
    active_index = next(
        index
        for index in table.indexes
        if index.name == "uq_document_ingestion_jobs_active_document"
    )

    assert active_index.unique is True
    assert "PENDING" in str(active_index.dialect_options["postgresql"]["where"])
    assert "RUNNING" in str(active_index.dialect_options["postgresql"]["where"])
    assert any(
        constraint.name == "uq_document_ingestion_jobs_idempotency_key"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_document_ingestion_jobs_attempts_nonnegative"
        for constraint in table.constraints
    )


def test_document_chunk_uses_fixed_vector_and_traceable_source_fields() -> None:
    """Chunk 必须使用固定向量维度，并能追溯到顺序、标题和页码。"""
    from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity

    table = DocumentChunkEntity.__table__
    embedding_type = table.c.embedding.type
    vector_index = next(
        index
        for index in table.indexes
        if index.name == "ix_document_chunks_embedding_hnsw_cosine"
    )

    assert embedding_type.__class__.__name__ == "VECTOR"
    assert embedding_type.dim == 1024
    assert table.c.chunk_index.nullable is False
    assert table.c.content.nullable is False
    assert table.c.heading_path.nullable is True
    assert table.c.page_start.nullable is True
    assert table.c.page_end.nullable is True
    assert vector_index.dialect_options["postgresql"]["using"] == "hnsw"
    assert (
        vector_index.dialect_options["postgresql"]["ops"]["embedding"]
        == "vector_cosine_ops"
    )


def test_rag_migration_follows_head_and_builds_vector_storage() -> None:
    """RAG 迁移必须接在当前 head，并完整创建扩展、表和 HNSW 索引。"""
    migration_files = list(
        Path("migrations/versions").glob("*_create_rag_knowledge_foundation.py")
    )

    assert len(migration_files) == 1
    migration = migration_files[0].read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "b9d5e7f2a410"' in migration
    assert 'op.execute("CREATE EXTENSION IF NOT EXISTS vector")' in migration
    assert '"knowledge_bases"' in migration
    assert '"documents"' in migration
    assert '"document_ingestion_jobs"' in migration
    assert '"document_chunks"' in migration
    assert "VECTOR(1024)" in migration
    assert 'postgresql_using="hnsw"' in migration
    assert 'postgresql_ops={"embedding": "vector_cosine_ops"}' in migration
    assert "DROP EXTENSION" not in migration


def test_compose_uses_pinned_pgvector_image_and_keeps_named_volume() -> None:
    """本地 PostgreSQL 必须具备 vector 扩展，同时继续复用原命名数据卷。"""
    compose = Path("../infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "pgvector/pgvector:0.8.2-pg17-trixie" in compose
    assert "postgres_data:/var/lib/postgresql/data" in compose
    assert "container_name: enterprise-postgres" in compose


def test_alembic_imports_knowledge_models_for_metadata_discovery() -> None:
    """Alembic 必须导入知识模块实体，否则自动迁移无法发现新表。"""
    migration_env = Path("migrations/env.py").read_text(encoding="utf-8")

    assert "from app.modules.knowledge import models" in migration_env
