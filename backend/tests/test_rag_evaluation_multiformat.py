import asyncio
import re
from collections import Counter
from io import BytesIO
from pathlib import Path

from app.common.enums import SourceType
from app.infrastructure.llm.token_counter import TokenCounter
from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.metrics import RagMetricsEvaluator
from app.modules.knowledge.evaluation.models import (
    RagEvaluationObservation,
    RagEvaluationReport,
    RagExpectedBehavior,
    RetrievedEvidence,
)
from app.modules.knowledge.processing.chunker import TokenAwareChunker
from app.modules.knowledge.processing.parser_registry import DocumentParserRegistry
from app.modules.knowledge.source_locator import infer_source_type

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIRECTORY = BACKEND_ROOT / "evaluation" / "corpus" / "multiformat-v3"
DATASET_PATH = BACKEND_ROOT / "evaluation" / "datasets" / "rag-v3-multiformat.jsonl"
BASELINE_PATH = (
    BACKEND_ROOT / "evaluation" / "baselines" / "rag-v3-multiformat-mock.json"
)
EXPECTED_FILES = {
    "atlas-overview.md",
    "borealis-sla.txt",
    "cetus-policy.pdf",
    "draco-meeting.docx",
    "equuleus-budget.xlsx",
    "fornax-scan.png",
    "gemini-scan.pdf",
}
EXPECTED_FORMAT_TAGS = {
    "Markdown",
    "TXT",
    "文本 PDF",
    "DOCX",
    "XLSX",
    "图片 OCR",
    "扫描 PDF OCR",
}


def test_multiformat_dataset_has_balanced_formats_and_refusals() -> None:
    """正式多格式集应覆盖七类来源，并为每类准备回答题和拒答干扰题。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    behavior_counts = Counter(case.expected_behavior for case in cases)
    all_tags = {tag for case in cases for tag in case.tags}

    assert len(cases) == 35
    assert behavior_counts[RagExpectedBehavior.ANSWER] == 28
    assert behavior_counts[RagExpectedBehavior.REFUSE] == 7
    assert EXPECTED_FORMAT_TAGS.issubset(all_tags)
    assert set(path.name for path in CORPUS_DIRECTORY.iterdir() if path.is_file()) == (
        EXPECTED_FILES
    )
    assert all(
        evidence.source_type is not None and evidence.source_locator is not None
        for case in cases
        if case.expected_behavior == RagExpectedBehavior.ANSWER
        for evidence in case.expected_evidence
    )


def test_multiformat_evidence_and_locations_match_real_parser_output() -> None:
    """所有标准证据必须经过真实 Parser/Chunker 找到且结构化位置完全正确。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    chunks_by_filename = asyncio.run(parse_all_corpus_documents())
    observations: list[RagEvaluationObservation] = []
    for case in cases:
        if case.expected_behavior == RagExpectedBehavior.REFUSE:
            observations.append(
                RagEvaluationObservation(
                    case_id=case.case_id,
                    retrieved_sources=(),
                    refused=True,
                )
            )
            continue
        relevant_files = {evidence.filename for evidence in case.expected_evidence}
        sources = tuple(
            source
            for filename in sorted(relevant_files)
            for source in chunks_by_filename[filename]
        )
        observations.append(
            RagEvaluationObservation(
                case_id=case.case_id,
                retrieved_sources=sources,
                refused=False,
            )
        )

    report = RagMetricsEvaluator(top_k=20).evaluate(cases, tuple(observations))

    assert report.recall_at_k == 1.0
    assert report.source_location_accuracy == 1.0
    assert all(
        result.source_location_accuracy == 1.0
        for result in report.results
        if result.recall_at_k is not None
    )


def test_multiformat_mock_baseline_is_complete_and_sanitized() -> None:
    """版本化 Mock 基线应覆盖全部问题，且不保存本机用户、知识库或 Chunk UUID。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(DATASET_PATH)
    report = RagEvaluationReport.model_validate_json(
        BASELINE_PATH.read_text(encoding="utf-8")
    )
    raw = BASELINE_PATH.read_text(encoding="utf-8")

    assert report.retrieval_strategy == "offline_mock_vector"
    assert report.embedding_model == "mock-hash-1024"
    assert report.top_k == 3
    assert report.total_cases == len(cases)
    assert report.source_location_accuracy is not None
    assert report.average_latency_ms is None
    assert report.p95_latency_ms is None
    assert [result.case_id for result in report.results] == [
        case.case_id for case in cases
    ]
    assert all(not result.retrieved_sources for result in report.results)
    assert all(result.latency_ms is None for result in report.results)
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        raw,
        flags=re.IGNORECASE,
    )


async def parse_all_corpus_documents() -> dict[str, tuple[RetrievedEvidence, ...]]:
    """使用同一 OCR Provider 解析全部语料，并转换为评测来源。"""
    registry = DocumentParserRegistry()
    token_counter = TokenCounter("mock-hash-1024", encoder=False)
    chunker = TokenAwareChunker(
        token_counter=token_counter,
        chunk_size_tokens=600,
        chunk_overlap_tokens=80,
    )
    result: dict[str, tuple[RetrievedEvidence, ...]] = {}
    for path in sorted(CORPUS_DIRECTORY.iterdir()):
        parser = registry.get_parser(path.name)
        parsed = await parser.parse(BytesIO(path.read_bytes()))
        source_type = infer_source_type(path.name)
        result[path.name] = tuple(
            RetrievedEvidence(
                source_id=f"{path.name}:{chunk.chunk_index}",
                filename=path.name,
                content=chunk.content,
                source_type=source_type,
                source_locator=chunk.source_locator,
            )
            for chunk in chunker.chunk(parsed)
        )
    assert {
        SourceType(source.source_type)
        for sources in result.values()
        for source in sources
    } >= {
        SourceType.MARKDOWN,
        SourceType.TEXT,
        SourceType.PDF,
        SourceType.DOCX,
        SourceType.XLSX,
        SourceType.IMAGE,
    }
    return result
