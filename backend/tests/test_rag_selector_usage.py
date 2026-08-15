from pathlib import Path

import pytest

from app.modules.knowledge.evaluation.cli import build_parser
from app.modules.knowledge.evaluation.models import RagSelectorUsageSummary
from app.modules.knowledge.evaluation.selector_usage import (
    LLMEvidenceUsageRecorder,
    RagSelectorUsageError,
)
from app.modules.knowledge.retrieval.llm_evidence_selector import (
    LLMEvidenceCallMetrics,
)


def test_usage_recorder_aggregates_tokens_latency_and_conservative_cost() -> None:
    """评测报告应汇总真实用量，并按缓存未命中单价给出可复算估价。"""
    recorder = LLMEvidenceUsageRecorder()
    recorder.record(
        LLMEvidenceCallMetrics(
            model="deepseek-v4-flash",
            prompt_tokens=1_000,
            completion_tokens=100,
            total_tokens=1_100,
            latency_ms=20.0,
        )
    )
    recorder.record(
        LLMEvidenceCallMetrics(
            model="deepseek-v4-flash",
            prompt_tokens=2_000,
            completion_tokens=200,
            total_tokens=2_200,
            latency_ms=40.0,
        )
    )

    summary = recorder.build_summary(
        provider="deepseek",
        expected_model="deepseek-v4-flash",
        thinking_enabled=False,
        input_price_per_million_usd=0.14,
        output_price_per_million_usd=0.28,
    )

    expected = RagSelectorUsageSummary(
        provider="deepseek",
        model="deepseek-v4-flash",
        thinking_enabled=False,
        calls=2,
        prompt_tokens=3_000,
        completion_tokens=300,
        total_tokens=3_300,
        average_latency_ms=30.0,
        p95_latency_ms=40.0,
        input_price_per_million_usd=0.14,
        output_price_per_million_usd=0.28,
        estimated_cost_usd=0.000504,
        pricing_basis="cache_miss",
    )
    assert summary.model_dump(exclude={"estimated_cost_usd"}) == expected.model_dump(
        exclude={"estimated_cost_usd"}
    )
    assert summary.estimated_cost_usd == pytest.approx(0.000504)


def test_usage_recorder_rejects_model_drift() -> None:
    """接口实际返回其他模型时必须终止报告，避免把费用和质量归错模型。"""
    recorder = LLMEvidenceUsageRecorder()
    recorder.record(
        LLMEvidenceCallMetrics(
            model="unexpected-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
        )
    )

    with pytest.raises(RagSelectorUsageError, match="模型"):
        recorder.build_summary(
            provider="deepseek",
            expected_model="deepseek-v4-flash",
            thinking_enabled=False,
            input_price_per_million_usd=0.14,
            output_price_per_million_usd=0.28,
        )


def test_evaluation_cli_requires_explicit_llm_selector_switch() -> None:
    """默认评测不得产生 LLM 费用，必须由调用者显式选择 llm。"""
    parser = build_parser()
    common = [
        "--dataset",
        str(Path("evaluation/datasets/rag-v2-challenge.jsonl")),
        "--user-id",
        "00000000-0000-4000-8000-000000000001",
        "--knowledge-base-id",
        "00000000-0000-4000-8000-000000000002",
    ]

    safe_args = parser.parse_args(common)
    llm_args = parser.parse_args(common + ["--evidence-selector", "llm"])

    assert safe_args.evidence_selector == "threshold"
    assert llm_args.evidence_selector == "llm"
    assert llm_args.llm_provider == "mock"
    assert llm_args.llm_model == "deepseek-v4-flash"
    assert llm_args.llm_thinking is False
