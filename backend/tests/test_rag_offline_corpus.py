import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.common.enums import SourceType
from app.modules.knowledge.evaluation.models import (
    ExpectedEvidence,
    ExpectedSectionSourceLocator,
    RagEvaluationCase,
    RagExpectedBehavior,
)
from app.modules.knowledge.evaluation.offline_corpus import (
    OfflineCorpusError,
    OfflineCorpusSearchService,
    sanitize_report_for_baseline,
)
from app.modules.knowledge.evaluation.runner import RagRetrievalEvaluationRunner
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
)
from app.modules.knowledge.source_locator import SectionSourceLocator


def test_offline_corpus_uses_real_parser_chunker_and_mock_embedding(
    tmp_path: Path,
) -> None:
    """离线格式回归应串起正式 Parser、Chunker 和 Mock Embedding，而不是硬编码结果。"""
    (tmp_path / "project.md").write_text(
        "# 会议安排\n\n天琴项目复盘会议在星云厅举行。",
        encoding="utf-8",
    )

    service = asyncio.run(
        OfflineCorpusSearchService.build(
            corpus_directory=tmp_path,
            similarity_threshold=0.0,
        )
    )
    response = asyncio.run(
        service.search(
            user_id=uuid4(),
            knowledge_base_id=service.knowledge_base_id,
            request=KnowledgeSearchRequest(query="复盘会议在哪里？", top_k=3),
        )
    )

    assert response.embedding_model == "mock-hash-1024"
    assert response.similarity_threshold == 0.0
    assert response.results[0].filename == "project.md"
    assert "星云厅" in response.results[0].content
    assert response.results[0].source_type == SourceType.MARKDOWN
    assert response.results[0].source_locator == SectionSourceLocator(
        heading_path=("会议安排",)
    )


def test_offline_corpus_rejects_empty_or_unsupported_directories(
    tmp_path: Path,
) -> None:
    """空语料或混入未知格式不能被静默忽略，否则评测分母会失真。"""
    with pytest.raises(OfflineCorpusError, match="至少包含一份"):
        asyncio.run(OfflineCorpusSearchService.build(corpus_directory=tmp_path))

    (tmp_path / "unknown.csv").write_text("a,b", encoding="utf-8")
    with pytest.raises(OfflineCorpusError, match="不支持的评测语料"):
        asyncio.run(OfflineCorpusSearchService.build(corpus_directory=tmp_path))


def test_sanitized_baseline_keeps_metrics_without_source_ids(tmp_path: Path) -> None:
    """版本化基线保留指标与位置结果，但删除本地 Chunk 明细和稳定 UUID。"""
    (tmp_path / "project.md").write_text(
        "# 负责人\n\n项目负责人是林晓。",
        encoding="utf-8",
    )
    service = asyncio.run(
        OfflineCorpusSearchService.build(
            corpus_directory=tmp_path,
            similarity_threshold=0.0,
        )
    )
    case = RagEvaluationCase(
        case_id="project-owner",
        question="负责人是谁？",
        expected_behavior=RagExpectedBehavior.ANSWER,
        expected_evidence=(
            ExpectedEvidence(
                filename="project.md",
                content_contains="负责人是林晓",
                source_type=SourceType.MARKDOWN,
                source_locator=ExpectedSectionSourceLocator(heading_path=("负责人",)),
            ),
        ),
    )
    report = asyncio.run(
        RagRetrievalEvaluationRunner(search_service=service).run(
            cases=(case,),
            user_id=uuid4(),
            knowledge_base_id=service.knowledge_base_id,
        )
    )

    sanitized = sanitize_report_for_baseline(report)

    assert sanitized.recall_at_k == 1.0
    assert sanitized.source_location_accuracy == 1.0
    assert sanitized.average_latency_ms is None
    assert sanitized.p95_latency_ms is None
    assert sanitized.results[0].retrieved_sources == ()
    assert sanitized.results[0].latency_ms is None
