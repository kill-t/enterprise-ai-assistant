import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.knowledge.evaluation.metrics import RagMetricsEvaluator
from app.modules.knowledge.evaluation.models import (
    ExpectedEvidence,
    RagEvaluationCase,
    RagExpectedBehavior,
)
from app.modules.knowledge.evaluation.report_writer import RagEvaluationReportWriter
from app.modules.knowledge.evaluation.runner import (
    RagEvaluationRunError,
    RagRetrievalEvaluationRunner,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchHit,
    KnowledgeSearchResponse,
)


def test_retrieval_runner_calls_search_service_and_builds_metrics() -> None:
    """Runner 应执行真实检索契约，并把来源、拒答和延迟交给指标层。"""
    user_id = uuid4()
    knowledge_base_id = uuid4()
    relevant_chunk_id = uuid4()
    search_service = SimpleNamespace(
        search=AsyncMock(
            side_effect=[
                build_search_response(
                    knowledge_base_id=knowledge_base_id,
                    query="项目负责人是谁？",
                    results=[
                        build_hit(
                            chunk_id=relevant_chunk_id,
                            filename="project.md",
                            content="蓝鲸项目负责人是林晓。",
                            score=0.91,
                        )
                    ],
                ),
                build_search_response(
                    knowledge_base_id=knowledge_base_id,
                    query="量子燃料配额是多少？",
                    results=[],
                ),
            ]
        )
    )
    cases = (
        RagEvaluationCase(
            case_id="project-owner",
            question="项目负责人是谁？",
            expected_behavior=RagExpectedBehavior.ANSWER,
            expected_evidence=[
                ExpectedEvidence(
                    filename="project.md",
                    content_contains="负责人是林晓",
                )
            ],
        ),
        RagEvaluationCase(
            case_id="unknown-quota",
            question="量子燃料配额是多少？",
            expected_behavior=RagExpectedBehavior.REFUSE,
        ),
    )
    # 每个问题读取两次时钟：开始和结束，分别模拟 12ms 与 4ms。
    clock_values = iter((10.0, 10.012, 20.0, 20.004))
    runner = RagRetrievalEvaluationRunner(
        search_service=search_service,
        evaluator=RagMetricsEvaluator(top_k=3),
        clock=lambda: next(clock_values),
    )

    report = asyncio.run(
        runner.run(
            cases=cases,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
    )

    assert search_service.search.await_count == 2
    first_request = search_service.search.await_args_list[0].kwargs["request"]
    assert first_request.query == "项目负责人是谁？"
    assert first_request.top_k == 3
    assert search_service.search.await_args_list[0].kwargs["user_id"] == user_id
    assert (
        search_service.search.await_args_list[0].kwargs["knowledge_base_id"]
        == knowledge_base_id
    )
    assert report.recall_at_k == 1.0
    assert report.mean_reciprocal_rank == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.embedding_model == "mock-hash-1024"
    assert report.similarity_threshold == 0.2
    # 只执行检索，没有运行回答和引用生成，因此引用指标必须明确为不适用。
    assert report.citation_validity_rate is None
    assert report.average_latency_ms == pytest.approx(8.0)
    assert report.p95_latency_ms == pytest.approx(12.0)
    assert report.results[0].latency_ms == pytest.approx(12.0)
    assert report.results[0].retrieved_count == 1
    assert report.results[0].top_score == 0.91
    assert report.results[0].retrieved_sources[0].rank == 1
    assert report.results[0].retrieved_sources[0].relevant is True
    assert report.results[1].retrieved_count == 0
    assert report.results[1].top_score is None
    assert report.results[1].latency_ms == pytest.approx(4.0)


def test_retrieval_runner_wraps_failure_with_case_id() -> None:
    """单题检索失败应终止评测并指出 case_id，不能静默跳过后得到虚假高分。"""
    search_service = SimpleNamespace(
        search=AsyncMock(side_effect=ConnectionError("内部连接细节"))
    )
    runner = RagRetrievalEvaluationRunner(search_service=search_service)
    case = RagEvaluationCase(
        case_id="failing-case",
        question="会失败的问题",
        expected_behavior=RagExpectedBehavior.REFUSE,
    )

    with pytest.raises(RagEvaluationRunError, match="failing-case") as error:
        asyncio.run(
            runner.run(
                cases=(case,),
                user_id=uuid4(),
                knowledge_base_id=uuid4(),
            )
        )

    assert "内部连接细节" not in str(error.value)


def test_report_writer_outputs_utf8_json_and_readable_markdown(tmp_path: Path) -> None:
    """报告应同时支持机器比较和人工审查，并清楚显示未执行的引用指标。"""
    report = asyncio.run(build_report_for_writer())
    writer = RagEvaluationReportWriter()

    artifacts = writer.write(
        report=report,
        output_directory=tmp_path,
        report_name="mock-baseline",
    )

    payload = json.loads(artifacts.json_path.read_text(encoding="utf-8"))
    markdown = artifacts.markdown_path.read_text(encoding="utf-8")
    assert payload["total_cases"] == 1
    assert payload["citation_validity_rate"] is None
    assert payload["results"][0]["case_id"] == "owner-case"
    assert "# RAG 评测报告" in markdown
    assert "Recall@1" in markdown
    assert "引用有效率 | 不适用" in markdown
    assert "整体行为准确率" in markdown
    assert "拒答题准确率" in markdown
    assert "## 按标签诊断" in markdown
    assert "Top 分数" in markdown
    assert "owner-case" in markdown

    with pytest.raises(ValueError, match="报告名称"):
        writer.write(
            report=report,
            output_directory=tmp_path,
            report_name="../escape",
        )
    assert not (tmp_path.parent / "escape.json").exists()


async def build_report_for_writer():
    """通过真实 Runner 构造报告，避免 Writer 测试复制报告内部字段。"""
    knowledge_base_id = uuid4()
    response = build_search_response(
        knowledge_base_id=knowledge_base_id,
        query="负责人是谁？",
        results=[
            build_hit(
                chunk_id=uuid4(),
                filename="project.md",
                content="负责人是林晓。",
                score=0.9,
            )
        ],
    )
    service = SimpleNamespace(search=AsyncMock(return_value=response))
    clock_values = iter((1.0, 1.005))
    runner = RagRetrievalEvaluationRunner(
        search_service=service,
        evaluator=RagMetricsEvaluator(top_k=1),
        clock=lambda: next(clock_values),
    )
    case = RagEvaluationCase(
        case_id="owner-case",
        question="负责人是谁？",
        expected_behavior=RagExpectedBehavior.ANSWER,
        expected_evidence=[
            ExpectedEvidence(
                filename="project.md",
                content_contains="负责人是林晓",
            )
        ],
    )
    return await runner.run(
        cases=(case,),
        user_id=uuid4(),
        knowledge_base_id=knowledge_base_id,
    )


def build_search_response(
    *,
    knowledge_base_id: UUID,
    query: str,
    results: list[KnowledgeSearchHit],
) -> KnowledgeSearchResponse:
    """构造与在线检索 Service 完全相同的响应契约。"""
    return KnowledgeSearchResponse(
        knowledge_base_id=knowledge_base_id,
        query=query,
        embedding_model="mock-hash-1024",
        similarity_threshold=0.2,
        results=results,
    )


def build_hit(
    *,
    chunk_id: UUID,
    filename: str,
    content: str,
    score: float,
) -> KnowledgeSearchHit:
    """构造一条带来源位置的模拟 Chunk。"""
    return KnowledgeSearchHit(
        chunk_id=chunk_id,
        document_id=uuid4(),
        filename=filename,
        chunk_index=0,
        content=content,
        heading_path="项目资料",
        page_start=None,
        page_end=None,
        token_count=20,
        score=score,
    )
