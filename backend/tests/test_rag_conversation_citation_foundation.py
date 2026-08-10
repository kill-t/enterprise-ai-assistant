from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.modules.conversation.entity.conversation_knowledge_base import (
    ConversationKnowledgeBaseEntity,
)
from app.modules.conversation.entity.message_citation import MessageCitationEntity


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "e8f4a1c7d205_create_conversation_kb_bindings_and_citations.py"
)


def test_conversation_knowledge_base_uses_natural_composite_primary_key() -> None:
    """会话与知识库的组合应天然去重，不需要额外无业务意义的 UUID。"""
    table = ConversationKnowledgeBaseEntity.__table__

    assert table.name == "conversation_knowledge_bases"
    assert set(table.primary_key.columns.keys()) == {
        "conversation_id",
        "knowledge_base_id",
    }
    assert table.c.created_at.nullable is False

    foreign_keys = foreign_key_contracts(table)
    assert foreign_keys["conversation_id"] == ("conversations.id", "CASCADE")
    assert foreign_keys["knowledge_base_id"] == ("knowledge_bases.id", "CASCADE")
    assert "ix_conversation_knowledge_bases_knowledge_base_id" in {
        index.name for index in table.indexes
    }


def test_message_citation_keeps_snapshots_when_live_sources_disappear() -> None:
    """Chunk、文档或知识库删除后，引用外键可置空但展示快照必须保留。"""
    table = MessageCitationEntity.__table__

    assert table.name == "message_citations"
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert table.c.message_id.nullable is False
    assert table.c.knowledge_base_id.nullable is True
    assert table.c.document_id.nullable is True
    assert table.c.chunk_id.nullable is True

    for snapshot_column in (
        "knowledge_base_name_snapshot",
        "filename_snapshot",
        "chunk_index_snapshot",
        "content_snapshot",
        "source_order",
        "retrieval_score",
    ):
        assert table.c[snapshot_column].nullable is False

    foreign_keys = foreign_key_contracts(table)
    assert foreign_keys["message_id"] == ("messages.id", "CASCADE")
    assert foreign_keys["knowledge_base_id"] == (
        "knowledge_bases.id",
        "SET NULL",
    )
    assert foreign_keys["document_id"] == ("documents.id", "SET NULL")
    assert foreign_keys["chunk_id"] == ("document_chunks.id", "SET NULL")


def test_message_citation_has_order_score_page_and_reverse_lookup_guards() -> None:
    """引用顺序、分数和页码必须有数据库约束，外键删除也应有索引支持。"""
    table = MessageCitationEntity.__table__
    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }
    index_names = {index.name for index in table.indexes}

    assert "uq_message_citations_message_source_order" in constraint_names
    assert "ck_message_citations_source_order_positive" in constraint_names
    assert "ck_message_citations_score_range" in constraint_names
    assert "ck_message_citations_chunk_index_nonnegative" in constraint_names
    assert "ck_message_citations_page_start_positive" in constraint_names
    assert "ck_message_citations_page_end_positive" in constraint_names
    assert "ck_message_citations_page_order" in constraint_names
    assert {
        "ix_message_citations_knowledge_base_id",
        "ix_message_citations_document_id",
        "ix_message_citations_chunk_id",
    }.issubset(index_names)


def test_migration_creates_and_drops_tables_in_dependency_order() -> None:
    """迁移必须接在当前 head，并按外键依赖顺序创建和回滚两张表。"""
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision: str = "e8f4a1c7d205"' in source
    assert 'down_revision: Union[str, Sequence[str], None] = "c2a7d4e9f103"' in source
    assert source.index('op.create_table(\n        "conversation_knowledge_bases"') < source.index(
        'op.create_table(\n        "message_citations"'
    )
    assert source.index('op.drop_table("message_citations")') < source.index(
        'op.drop_table("conversation_knowledge_bases")'
    )
    assert 'ondelete="SET NULL"' in source


def test_alembic_imports_conversation_model_aggregate() -> None:
    """Alembic 必须导入会话聚合模型，否则自动迁移会漏掉新增实体。"""
    env_path = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
    source = env_path.read_text(encoding="utf-8")

    assert "from app.modules.conversation import models as conversation_models" in source


def foreign_key_contracts(table) -> dict[str, tuple[str, str | None]]:
    """把单列外键整理成便于阅读的字段、目标和删除策略映射。"""
    contracts: dict[str, tuple[str, str | None]] = {}
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        element = next(iter(constraint.elements))
        contracts[element.parent.name] = (
            element.target_fullname,
            constraint.ondelete,
        )
    return contracts
