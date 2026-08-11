"""提供可在本地或 CI 中运行的 RAG 检索评测命令。"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.config import settings
from app.infrastructure.database.session import SessionLocal
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.task_queue.asyncio_runner import run_async
from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetError,
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.metrics import RagEvaluationError, RagMetricsEvaluator
from app.modules.knowledge.evaluation.models import (
    RagEvaluationCase,
    RagEvaluationReport,
)
from app.modules.knowledge.evaluation.report_writer import RagEvaluationReportWriter
from app.modules.knowledge.evaluation.runner import (
    RagEvaluationRunError,
    RagRetrievalEvaluationRunner,
)
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


def build_parser() -> argparse.ArgumentParser:
    """定义显式用户、知识库、Top-K 和报告路径参数。"""
    parser = argparse.ArgumentParser(description="运行 Enterprise AI Assistant RAG 检索评测")
    parser.add_argument("--dataset", type=Path, required=True, help="JSONL 评测集路径")
    parser.add_argument("--user-id", type=UUID, required=True, help="知识库所属用户 UUID")
    parser.add_argument(
        "--knowledge-base-id",
        type=UUID,
        required=True,
        help="本次评测使用的知识库 UUID",
    )
    parser.add_argument("--top-k", type=int, default=5, help="检索返回数量，范围 1 到 20")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("evaluation/reports"),
        help="JSON 和 Markdown 报告输出目录",
    )
    parser.add_argument(
        "--report-name",
        default=None,
        help="报告文件名，不包含扩展名；默认使用 UTC 时间",
    )
    return parser


async def execute_evaluation(
    *,
    cases: tuple[RagEvaluationCase, ...],
    user_id: UUID,
    knowledge_base_id: UUID,
    top_k: int,
) -> RagEvaluationReport:
    """在一个只读数据库 Session 中组装在线检索 Service 并运行评测。"""
    async with SessionLocal() as db:
        search_service = KnowledgeSearchService(
            db=db,
            embedding_factory=EmbeddingProviderFactory(),
            similarity_threshold=settings.rag.search_similarity_threshold,
            candidate_multiplier=settings.rag.search_candidate_multiplier,
        )
        runner = RagRetrievalEvaluationRunner(
            search_service=search_service,
            evaluator=RagMetricsEvaluator(top_k=top_k),
        )
        return await runner.run(
            cases=cases,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """加载数据、运行异步检索并输出两种报告；失败时返回非零退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cases = RagEvaluationDatasetLoader().load_jsonl(args.dataset)
        report = run_async(
            execute_evaluation(
                cases=cases,
                user_id=args.user_id,
                knowledge_base_id=args.knowledge_base_id,
                top_k=args.top_k,
            )
        )
        report_name = args.report_name or datetime.now(UTC).strftime(
            "rag-evaluation-%Y%m%d-%H%M%S"
        )
        artifacts = RagEvaluationReportWriter().write(
            report=report,
            output_directory=args.output_directory,
            report_name=report_name,
        )
    except (
        RagEvaluationDatasetError,
        RagEvaluationError,
        RagEvaluationRunError,
        ValueError,
    ) as exc:
        parser.exit(status=1, message=f"RAG 评测失败：{exc}\n")

    print(f"RAG 评测完成：{report.total_cases} 个样例")
    print(f"JSON 报告：{artifacts.json_path.resolve()}")
    print(f"Markdown 报告：{artifacts.markdown_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
