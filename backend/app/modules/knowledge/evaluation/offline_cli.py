"""提供无需数据库和 API Key 的多格式 RAG 离线回归命令。"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.infrastructure.task_queue.asyncio_runner import run_async
from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetError,
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.metrics import (
    RagEvaluationError,
    RagMetricsEvaluator,
)
from app.modules.knowledge.evaluation.models import RagEvaluationReport
from app.modules.knowledge.evaluation.offline_corpus import (
    OfflineCorpusError,
    OfflineCorpusSearchService,
    sanitize_report_for_baseline,
)
from app.modules.knowledge.evaluation.report_writer import RagEvaluationReportWriter
from app.modules.knowledge.evaluation.runner import (
    RagEvaluationRunError,
    RagRetrievalEvaluationRunner,
)

OFFLINE_USER_ID = uuid5(
    NAMESPACE_URL,
    "enterprise-ai-assistant:offline-evaluation-user",
)


def build_parser() -> argparse.ArgumentParser:
    """定义离线语料、数据集、阈值和报告输出参数。"""
    parser = argparse.ArgumentParser(description="运行多格式 RAG 离线 Mock 评测")
    parser.add_argument("--corpus-directory", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--similarity-threshold", type=float, default=0.2)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("evaluation/reports"),
    )
    parser.add_argument("--report-name", default=None)
    parser.add_argument(
        "--sanitized-baseline",
        type=Path,
        default=None,
        help="可选：额外写入不含来源 ID 的版本化 JSON 基线",
    )
    return parser


async def execute_offline_evaluation(
    args: argparse.Namespace,
) -> RagEvaluationReport:
    """建立本地索引、复用正式 Runner，并标记离线评测策略。"""
    cases = RagEvaluationDatasetLoader().load_jsonl(args.dataset)
    service = await OfflineCorpusSearchService.build(
        corpus_directory=args.corpus_directory,
        similarity_threshold=args.similarity_threshold,
    )
    report = await RagRetrievalEvaluationRunner(
        search_service=service,
        evaluator=RagMetricsEvaluator(top_k=args.top_k),
    ).run(
        cases=cases,
        user_id=OFFLINE_USER_ID,
        knowledge_base_id=service.knowledge_base_id,
    )
    return report.model_copy(
        update={
            "retrieval_strategy": "offline_mock_vector",
            "evidence_selector": "threshold",
        }
    )


def main(argv: Sequence[str] | None = None) -> int:
    """执行评测并输出完整本地报告，以及可选的脱敏版本化基线。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_async(execute_offline_evaluation(args))
        report_name = args.report_name or datetime.now(UTC).strftime(
            "rag-multiformat-%Y%m%d-%H%M%S"
        )
        artifacts = RagEvaluationReportWriter().write(
            report=report,
            output_directory=args.output_directory,
            report_name=report_name,
        )
        if args.sanitized_baseline is not None:
            baseline = sanitize_report_for_baseline(report)
            args.sanitized_baseline.parent.mkdir(parents=True, exist_ok=True)
            args.sanitized_baseline.write_text(
                baseline.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
    except (
        OfflineCorpusError,
        RagEvaluationDatasetError,
        RagEvaluationError,
        RagEvaluationRunError,
        ValueError,
    ) as exc:
        parser.exit(status=1, message=f"多格式 RAG 评测失败：{exc}\n")

    print(f"多格式 RAG 评测完成：{report.total_cases} 个样例")
    print(f"JSON 报告：{artifacts.json_path.resolve()}")
    print(f"Markdown 报告：{artifacts.markdown_path.resolve()}")
    if args.sanitized_baseline is not None:
        print(f"脱敏基线：{args.sanitized_baseline.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
