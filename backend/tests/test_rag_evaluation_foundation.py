import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.common.enums import SourceType
from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetError,
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.metrics import (
    RagEvaluationError,
    RagMetricsEvaluator,
)
from app.modules.knowledge.evaluation.models import (
    ExpectedEvidence,
    ExpectedPageSourceLocator,
    RagEvaluationCase,
    RagEvaluationObservation,
    RagExpectedBehavior,
    RetrievedEvidence,
)
from app.modules.knowledge.source_locator import PageSourceLocator


def test_evaluation_case_normalizes_text_and_protects_behavior_contract() -> None:
    """可回答问题必须提供标准证据，拒答问题则不能伪造标准证据。"""
    case = RagEvaluationCase(
        case_id="  meeting-owner  ",
        question="  项目负责人是谁？  ",
        expected_behavior=RagExpectedBehavior.ANSWER,
        expected_evidence=[
            ExpectedEvidence(
                filename="  project.md  ",
                content_contains="  项目负责人是林晓  ",
            )
        ],
        tags=[" 会议 ", "负责人"],
    )

    assert case.case_id == "meeting-owner"
    assert case.question == "项目负责人是谁？"
    assert case.expected_evidence[0].filename == "project.md"
    assert case.expected_evidence[0].content_contains == "项目负责人是林晓"
    assert case.tags == ("会议", "负责人")

    with pytest.raises(ValidationError, match="至少提供一条标准证据"):
        RagEvaluationCase(
            case_id="missing-evidence",
            question="负责人是谁？",
            expected_behavior=RagExpectedBehavior.ANSWER,
        )

    with pytest.raises(ValidationError, match="拒答样例不能配置标准证据"):
        RagEvaluationCase(
            case_id="should-refuse",
            question="资料中不存在的问题",
            expected_behavior=RagExpectedBehavior.REFUSE,
            expected_evidence=[
                ExpectedEvidence(filename="wrong.md", content_contains="不存在")
            ],
        )


def test_jsonl_loader_reads_cases_and_reports_invalid_line(tmp_path: Path) -> None:
    """加载器应忽略空行，并把格式错误准确定位到 JSONL 行号。"""
    dataset_path = tmp_path / "rag-evaluation.jsonl"
    valid_case = {
        "case_id": "meeting-time",
        "question": "会议什么时候举行？",
        "expected_behavior": "answer",
        "expected_evidence": [
            {
                "filename": "project.md",
                "content_contains": "2026 年 8 月 15 日 14:00",
            }
        ],
    }
    dataset_path.write_text(
        "\n" + json.dumps(valid_case, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cases = RagEvaluationDatasetLoader().load_jsonl(dataset_path)

    assert len(cases) == 1
    assert cases[0].case_id == "meeting-time"

    dataset_path.write_text(
        json.dumps(valid_case, ensure_ascii=False) + "\n{invalid-json}\n",
        encoding="utf-8",
    )

    with pytest.raises(RagEvaluationDatasetError, match="第 2 行"):
        RagEvaluationDatasetLoader().load_jsonl(dataset_path)


def test_jsonl_loader_rejects_duplicate_case_id(tmp_path: Path) -> None:
    """重复样例 ID 会覆盖结果关联，因此应在执行评测前直接拒绝。"""
    dataset_path = tmp_path / "duplicate.jsonl"
    payload = {
        "case_id": "duplicate-case",
        "question": "无法回答的问题",
        "expected_behavior": "refuse",
    }
    line = json.dumps(payload, ensure_ascii=False)
    dataset_path.write_text(f"{line}\n{line}\n", encoding="utf-8")

    with pytest.raises(RagEvaluationDatasetError, match="重复的 case_id"):
        RagEvaluationDatasetLoader().load_jsonl(dataset_path)


def test_metrics_calculate_recall_mrr_refusal_and_citation_validity() -> None:
    """汇总指标应区分证据召回、首条相关排名、拒答行为和引用有效性。"""
    cases = (
        build_answer_case(
            case_id="meeting",
            evidence=(
                ("project.md", "负责人是林晓"),
                ("project.md", "8 月 15 日 14:00"),
            ),
        ),
        build_answer_case(
            case_id="storage",
            evidence=(("architecture.md", "原始文件保存在 MinIO"),),
        ),
        RagEvaluationCase(
            case_id="unknown",
            question="量子航天器燃料配额是多少？",
            expected_behavior=RagExpectedBehavior.REFUSE,
        ),
    )
    observations = (
        RagEvaluationObservation(
            case_id="meeting",
            retrieved_sources=(
                build_source("S0", "other.md", "完全无关"),
                build_source("S1", "project.md", "会议负责人是林晓。"),
                build_source("S2", "project.md", "时间是 8 月 15 日 14:00。"),
            ),
            refused=False,
            cited_source_ids=("S1",),
        ),
        RagEvaluationObservation(
            case_id="storage",
            retrieved_sources=(
                build_source(
                    "S3",
                    "architecture.md",
                    "原始文件保存在 MinIO，向量进入 pgvector。",
                ),
            ),
            refused=False,
            cited_source_ids=("NOT_FOUND",),
        ),
        RagEvaluationObservation(
            case_id="unknown",
            retrieved_sources=(),
            refused=True,
            cited_source_ids=(),
        ),
    )

    report = RagMetricsEvaluator(top_k=2).evaluate(cases, observations)

    # meeting 在 Top-2 只召回两条标准证据中的一条，首条相关结果位于第 2 名。
    assert report.results[0].recall_at_k == 0.5
    assert report.results[0].reciprocal_rank == 0.5
    # storage 在第 1 名命中，因此与 meeting 平均后两个检索指标均为 0.75。
    assert report.recall_at_k == 0.75
    assert report.mean_reciprocal_rank == 0.75
    assert report.refusal_accuracy == 1.0
    # 三条结果中，meeting 和 unknown 的引用规则正确，storage 引用了未知来源。
    assert report.citation_validity_rate == pytest.approx(2 / 3)
    assert report.total_cases == 3
    assert report.answer_cases == 2
    assert report.refusal_cases == 1


def test_metrics_separate_overall_behavior_from_refusal_accuracy() -> None:
    """答案题误拒答只能降低整体行为准确率，不能污染拒答题专项指标。"""
    cases = (
        build_answer_case(
            case_id="known-but-missed",
            evidence=(("known.md", "资料中存在的答案"),),
        ).model_copy(update={"tags": ("同义改写",)}),
        RagEvaluationCase(
            case_id="unknown-and-refused",
            question="资料里没有答案的问题",
            expected_behavior=RagExpectedBehavior.REFUSE,
            tags=("困难拒答",),
        ),
    )
    observations = (
        RagEvaluationObservation(
            case_id="known-but-missed",
            retrieved_sources=(),
            refused=True,
        ),
        RagEvaluationObservation(
            case_id="unknown-and-refused",
            retrieved_sources=(),
            refused=True,
        ),
    )

    report = RagMetricsEvaluator(top_k=3).evaluate(cases, observations)

    assert report.behavior_accuracy == 0.5
    assert report.refusal_accuracy == 1.0
    assert report.results[0].behavior_correct is False
    assert report.results[1].behavior_correct is True
    assert [metric.tag for metric in report.tag_metrics] == ["同义改写", "困难拒答"]
    assert report.tag_metrics[0].behavior_accuracy == 0.0
    assert report.tag_metrics[0].refusal_accuracy is None
    assert report.tag_metrics[1].behavior_accuracy == 1.0
    assert report.tag_metrics[1].refusal_accuracy == 1.0


def test_source_location_accuracy_is_separate_from_content_recall() -> None:
    """原文命中但 PDF 页码错误时 Recall 保持命中，位置准确率必须失败。"""
    case = RagEvaluationCase(
        case_id="pdf-page-location",
        question="应急响应时间是多少？",
        expected_behavior=RagExpectedBehavior.ANSWER,
        expected_evidence=[
            ExpectedEvidence(
                filename="policy.pdf",
                content_contains="十五分钟内响应",
                source_type=SourceType.PDF,
                source_locator=ExpectedPageSourceLocator(
                    page_start=2,
                    page_end=2,
                ),
            )
        ],
        tags=("PDF",),
    )
    observation = RagEvaluationObservation(
        case_id=case.case_id,
        retrieved_sources=(
            RetrievedEvidence(
                source_id="S1",
                filename="policy.pdf",
                content="严重故障必须在十五分钟内响应。",
                source_type=SourceType.PDF,
                source_locator=PageSourceLocator(page_start=1, page_end=1),
            ),
        ),
        refused=False,
    )

    report = RagMetricsEvaluator(top_k=3).evaluate((case,), (observation,))

    assert report.recall_at_k == 1.0
    assert report.source_location_accuracy == 0.0
    assert report.results[0].source_location_accuracy == 0.0
    assert report.results[0].retrieved_sources[0].source_location_valid is False
    assert report.tag_metrics[0].source_location_accuracy == 0.0


def test_metrics_reject_missing_or_unexpected_observation() -> None:
    """样例与实际结果必须一一对应，防止漏跑问题后得到虚假的高分。"""
    case = RagEvaluationCase(
        case_id="known-case",
        question="未知问题",
        expected_behavior=RagExpectedBehavior.REFUSE,
    )
    unexpected = RagEvaluationObservation(
        case_id="other-case",
        retrieved_sources=(),
        refused=True,
    )

    with pytest.raises(RagEvaluationError, match="缺少.*known-case"):
        RagMetricsEvaluator(top_k=5).evaluate((case,), ())

    with pytest.raises(RagEvaluationError, match="未知.*other-case"):
        RagMetricsEvaluator(top_k=5).evaluate((case,), (unexpected,))


@pytest.mark.parametrize(
    ("models", "thresholds", "expected_message"),
    [
        (("model-a", "model-b"), (0.2, 0.2), "多个 Embedding 模型"),
        (("model-a", "model-a"), (0.2, 0.3), "多个相似度阈值"),
    ],
)
def test_metrics_reject_mixed_search_configuration(
    models: tuple[str, str],
    thresholds: tuple[float, float],
    expected_message: str,
) -> None:
    """同一基线不能混合模型或阈值，否则整体指标无法解释和复现。"""
    cases = tuple(
        RagEvaluationCase(
            case_id=f"case-{index}",
            question=f"未知问题 {index}",
            expected_behavior=RagExpectedBehavior.REFUSE,
        )
        for index in range(2)
    )
    observations = tuple(
        RagEvaluationObservation(
            case_id=case.case_id,
            retrieved_sources=(),
            refused=True,
            embedding_model=models[index],
            similarity_threshold=thresholds[index],
        )
        for index, case in enumerate(cases)
    )

    with pytest.raises(RagEvaluationError, match=expected_message):
        RagMetricsEvaluator(top_k=3).evaluate(cases, observations)


def build_answer_case(
    *,
    case_id: str,
    evidence: tuple[tuple[str, str], ...],
) -> RagEvaluationCase:
    """构造可回答样例，使指标测试只突出排名和召回公式。"""
    return RagEvaluationCase(
        case_id=case_id,
        question=f"{case_id} 对应的问题",
        expected_behavior=RagExpectedBehavior.ANSWER,
        expected_evidence=[
            ExpectedEvidence(filename=filename, content_contains=content)
            for filename, content in evidence
        ],
    )


def build_source(source_id: str, filename: str, content: str) -> RetrievedEvidence:
    """构造稳定来源 ID 的模拟检索结果。"""
    return RetrievedEvidence(
        source_id=source_id,
        filename=filename,
        content=content,
        score=0.8,
    )
