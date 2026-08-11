from collections import Counter
import json
from pathlib import Path
import re

from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.models import RagExpectedBehavior


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIRECTORY = BACKEND_ROOT / "evaluation" / "corpus" / "challenge-v2"
DATASET_PATH = BACKEND_ROOT / "evaluation" / "datasets" / "rag-v2-challenge.jsonl"
COMPARISON_PATH = (
    BACKEND_ROOT / "evaluation" / "baselines" / "rag-v2-challenge-comparison.json"
)
EXPECTED_CORPUS_FILES = {
    "blue-whale-project-charter.md",
    "blue-whale-operations-runbook.md",
    "blue-whale-security-policy.md",
    "white-dolphin-legacy-notes.md",
}


def test_challenge_dataset_has_expected_size_and_scenario_mix() -> None:
    """困难集必须同时覆盖改写、多文档、近似干扰和困难拒答。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    behavior_counts = Counter(case.expected_behavior for case in cases)
    all_tags = {tag for case in cases for tag in case.tags}

    assert len(cases) == 35
    assert behavior_counts[RagExpectedBehavior.ANSWER] == 25
    assert behavior_counts[RagExpectedBehavior.REFUSE] == 10
    assert {"同义改写", "多文档", "近似干扰", "困难拒答"}.issubset(all_tags)
    assert sum("多文档" in case.tags for case in cases) >= 5
    assert sum("近似干扰" in case.tags for case in cases) >= 5


def test_challenge_evidence_exists_in_versioned_corpus() -> None:
    """每条标准证据都要能回到指定文件，避免答案标签与语料静默漂移。"""
    corpus = {
        path.name: path.read_text(encoding="utf-8")
        for path in CORPUS_DIRECTORY.glob("*.md")
    }
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)

    assert set(corpus) == EXPECTED_CORPUS_FILES
    for case in cases:
        for evidence in case.expected_evidence:
            assert evidence.filename in corpus
            assert evidence.content_contains in corpus[evidence.filename]


def test_challenge_questions_and_case_ids_are_unique() -> None:
    """困难集不能通过重复问题凑数量，且应真正使用多份业务资料。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    questions = [case.question.casefold() for case in cases]
    case_ids = [case.case_id for case in cases]
    evidence_files = {
        evidence.filename
        for case in cases
        for evidence in case.expected_evidence
    }

    assert len(questions) == len(set(questions))
    assert len(case_ids) == len(set(case_ids))
    assert len(evidence_files) >= 3
    assert "white-dolphin-legacy-notes.md" in evidence_files


def test_refusal_cases_do_not_hide_known_answers_in_corpus() -> None:
    """拒答问题的关键未知实体不能原样出现在语料中，防止错误标注。"""
    corpus_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in CORPUS_DIRECTORY.glob("*.md")
    )
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    refusal_markers = {
        "blue-whale-budget": "采购预算",
        "blue-whale-room": "具体会议室",
        "blue-whale-ocr-date": "OCR 上线日期",
        "blue-whale-key-algorithm": "密钥轮换算法",
        "blue-whale-rto": "精确 RTO",
        "blue-whale-owner-phone": "林晓的手机号码",
        "white-dolphin-database-version": "MySQL 小版本号",
        "white-dolphin-retirement": "白海豚下线日期",
        "cross-project-headcount": "两个项目的总人数",
        "unknown-vendor-contract": "向量模型供应商合同编号",
    }
    refusal_cases = {
        case.case_id: case
        for case in cases
        if case.expected_behavior == RagExpectedBehavior.REFUSE
    }

    assert set(refusal_cases) == set(refusal_markers)
    for marker in refusal_markers.values():
        assert marker not in corpus_text


def test_challenge_baseline_records_comparable_runs_without_local_ids() -> None:
    """版本化对比必须使用同一数据集，并删除用户、知识库和 Chunk UUID。"""
    payload = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    runs = {run["provider"]: run for run in payload["runs"]}

    assert payload["dataset"] == "rag-v2-challenge.jsonl"
    assert payload["total_cases"] == 35
    assert payload["answer_cases"] == 25
    assert payload["refusal_cases"] == 10
    assert set(runs) == {"mock", "openai_compatible"}
    assert runs["mock"]["similarity_threshold"] == 0.2
    assert runs["openai_compatible"]["similarity_threshold"] == 0.45
    assert runs["openai_compatible"]["recall_at_3"] > runs["mock"]["recall_at_3"]
    assert runs["openai_compatible"]["refusal_accuracy"] == 0.1
    assert payload["decision"]["next_strategy"] == "two_stage_retrieval"
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        COMPARISON_PATH.read_text(encoding="utf-8"),
        flags=re.IGNORECASE,
    )
