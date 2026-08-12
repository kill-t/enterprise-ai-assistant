import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from app.common.enums import DocumentStatus
from app.modules.knowledge.repository.pg_trgm_lexical_repository import (
    PgTrgmLexicalRepository,
)
from app.modules.knowledge.retrieval.lexical_retriever import (
    LexicalRetrievalError,
    LexicalRetrievalRequest,
)
from app.modules.knowledge.retrieval.pg_trgm_lexical_retriever import (
    PgTrgmLexicalRetriever,
)


def test_pg_trgm_migration_enables_extension_and_gist_index() -> None:
    """迁移必须安装可信扩展并创建可支持 Top-K 距离排序的 GiST 索引。"""
    migration_files = list(
        Path("migrations/versions").glob("*_add_pg_trgm_lexical_search.py")
    )

    assert len(migration_files) == 1
    migration = migration_files[0].read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "e8f4a1c7d205"' in migration
    assert 'op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")' in migration
    assert '"ix_document_chunks_content_gist_trgm"' in migration
    assert 'postgresql_using="gist"' in migration
    assert 'postgresql_ops={"content": "gist_trgm_ops"}' in migration
    assert "DROP EXTENSION" not in migration


def test_chunk_model_declares_same_pg_trgm_index_as_migration() -> None:
    """ORM 元数据要声明迁移索引，避免 Alembic 自动比较时错误建议删除。"""
    from app.modules.knowledge.entity.document_chunk import DocumentChunkEntity

    index = next(
        item
        for item in DocumentChunkEntity.__table__.indexes
        if item.name == "ix_document_chunks_content_gist_trgm"
    )

    assert index.dialect_options["postgresql"]["using"] == "gist"
    assert index.dialect_options["postgresql"]["ops"]["content"] == "gist_trgm_ops"


def test_repository_query_uses_scope_ready_guard_and_trigram_distance() -> None:
    """关键词 SQL 必须先限制所有权和 READY 文档，再按 trigram 距离取候选。"""
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(all=list),
            ]
        )
    )
    repository = PgTrgmLexicalRepository(db)
    user_id = uuid4()
    knowledge_base_ids = (uuid4(), uuid4())

    results = asyncio.run(
        repository.search_candidates(
            user_id=user_id,
            knowledge_base_ids=knowledge_base_ids,
            query="蓝鲸验收时间",
            candidate_limit=12,
            minimum_similarity=0.1,
        )
    )

    assert db.execute.await_count == 2
    threshold_call, search_call = db.execute.await_args_list
    threshold_sql = str(threshold_call.args[0])
    assert "set_config" in threshold_sql
    assert threshold_call.args[1] == {"threshold": "0.1"}

    statement = search_call.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    params = compiled.params

    assert results == []
    assert "knowledge_bases.user_id" in sql
    assert "knowledge_bases.is_archived IS false" in sql
    assert "documents.status" in sql
    assert "knowledge_bases.id IN" in sql
    assert "word_similarity" in sql
    assert "<%" in sql
    assert "<<->" in sql
    assert "ORDER BY" in sql
    assert params["user_id_1"] == user_id
    assert params["status_1"] == DocumentStatus.READY
    assert set(params["id_1"]) == set(knowledge_base_ids)
    assert 12 in params.values()


def test_repository_maps_rows_to_complete_lexical_candidates() -> None:
    """Repository 返回值必须保留 RRF、EvidenceSelector 和引用需要的全部来源字段。"""
    row = SimpleNamespace(
        knowledge_base_id=uuid4(),
        knowledge_base_name="蓝鲸资料",
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="project.md",
        chunk_index=2,
        content="蓝鲸项目负责人是林晓",
        content_hash="a" * 64,
        heading_path="项目身份",
        page_start=None,
        page_end=None,
        token_count=18,
        lexical_score=0.75,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(all=lambda: [row]),
            ]
        )
    )

    results = asyncio.run(
        PgTrgmLexicalRepository(db).search_candidates(
            user_id=uuid4(),
            knowledge_base_ids=(row.knowledge_base_id,),
            query="项目负责人",
            candidate_limit=5,
            minimum_similarity=0.1,
        )
    )

    assert len(results) == 1
    assert results[0].chunk_id == row.chunk_id
    assert results[0].knowledge_base_name == "蓝鲸资料"
    assert results[0].content == "蓝鲸项目负责人是林晓"
    assert results[0].lexical_score == pytest.approx(0.75)


def test_retriever_delegates_scope_and_returns_validated_result() -> None:
    """Adapter 应原样传递权限范围和预算，并校验 Repository 的返回边界。"""
    request = LexicalRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(uuid4(),),
        query="MinIO 保存在哪里",
        candidate_limit=8,
    )
    repository = SimpleNamespace(search_candidates=AsyncMock(return_value=[]))
    retriever = PgTrgmLexicalRetriever(
        repository=repository,
        minimum_similarity=0.15,
    )

    result = asyncio.run(retriever.retrieve(request))

    assert result.candidates == ()
    repository.search_candidates.assert_awaited_once_with(
        user_id=request.user_id,
        knowledge_base_ids=request.knowledge_base_ids,
        query=request.query,
        candidate_limit=request.candidate_limit,
        minimum_similarity=0.15,
    )


def test_retriever_wraps_database_failure_without_leaking_details() -> None:
    """数据库或扩展故障要成为词法通道错误，不能把连接细节暴露到上层。"""
    internal_message = "postgresql://secret@internal:5432 extension missing"
    repository = SimpleNamespace(
        search_candidates=AsyncMock(
            side_effect=OperationalError("statement", {}, Exception(internal_message))
        )
    )
    retriever = PgTrgmLexicalRetriever(repository=repository)
    request = LexicalRetrievalRequest(
        user_id=uuid4(),
        knowledge_base_ids=(uuid4(),),
        query="项目负责人",
        candidate_limit=5,
    )

    with pytest.raises(LexicalRetrievalError) as error:
        asyncio.run(retriever.retrieve(request))

    assert internal_message not in str(error.value)
    assert "暂时不可用" in str(error.value)


@pytest.mark.parametrize("minimum_similarity", [-0.1, 1.1])
def test_retriever_rejects_invalid_similarity(minimum_similarity: float) -> None:
    """pg_trgm 最低相似度必须位于合法范围，避免生成永不命中或全命中的查询。"""
    with pytest.raises(ValueError, match="minimum_similarity"):
        PgTrgmLexicalRetriever(
            repository=SimpleNamespace(),
            minimum_similarity=minimum_similarity,
        )
