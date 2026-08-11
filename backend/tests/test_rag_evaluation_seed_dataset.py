from pathlib import Path

from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.models import RagExpectedBehavior
from app.modules.knowledge.evaluation.models import RagEvaluationReport


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = BACKEND_ROOT / "evaluation" / "corpus" / "enterprise-ai-rag-demo.md"
DATASET_PATH = BACKEND_ROOT / "evaluation" / "datasets" / "rag-v1-seed.jsonl"
BASELINE_PATH = (
    BACKEND_ROOT / "evaluation" / "baselines" / "mock-hash-1024-v1.json"
)


def test_seed_dataset_matches_controlled_corpus() -> None:
    """种子问题的每条标准证据都必须存在于版本化语料，防止两者静默漂移。"""
    corpus = CORPUS_PATH.read_text(encoding="utf-8")
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    answer_cases = [
        case
        for case in cases
        if case.expected_behavior == RagExpectedBehavior.ANSWER
    ]
    refusal_cases = [
        case
        for case in cases
        if case.expected_behavior == RagExpectedBehavior.REFUSE
    ]

    assert len(cases) == 14
    assert len(answer_cases) == 10
    assert len(refusal_cases) == 4
    assert any(len(case.expected_evidence) > 1 for case in answer_cases)
    assert {"单事实", "多证据", "系统边界", "拒答"}.issubset(
        {tag for case in cases for tag in case.tags}
    )

    for case in answer_cases:
        for evidence in case.expected_evidence:
            assert evidence.filename == CORPUS_PATH.name
            assert evidence.content_contains in corpus


def test_seed_dataset_has_unique_questions_and_balanced_refusal_topics() -> None:
    """种子集不能用重复问法凑数量，并应包含多个不同领域的无证据问题。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    normalized_questions = {case.question.casefold() for case in cases}
    refusal_topics = {
        tag
        for case in cases
        if case.expected_behavior == RagExpectedBehavior.REFUSE
        for tag in case.tags
        if tag != "拒答"
    }

    assert len(normalized_questions) == len(cases)
    assert len(refusal_topics) == 4


def test_mock_baseline_matches_seed_dataset_and_records_search_config() -> None:
    """版本化基线必须覆盖全部种子题，并明确记录模型、阈值和 Top-K。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    report = RagEvaluationReport.model_validate_json(
        BASELINE_PATH.read_text(encoding="utf-8")
    )

    assert report.embedding_model == "mock-hash-1024"
    assert report.similarity_threshold == 0.2
    assert report.top_k == 3
    assert report.total_cases == len(cases)
    assert [result.case_id for result in report.results] == [
        case.case_id for case in cases
    ]
