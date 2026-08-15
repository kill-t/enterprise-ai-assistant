"""提供可在本地或 CI 中运行的 RAG 检索评测命令。"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.config import settings
from app.infrastructure.database.session import SessionLocal
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.llm.providers.deepseek_provider import DeepSeekProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
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
from app.modules.knowledge.evaluation.selector_usage import (
    LLMEvidenceUsageRecorder,
    RagSelectorUsageError,
)
from app.modules.knowledge.retrieval.evidence_selection import (
    BaseEvidenceSelector,
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.retrieval.llm_evidence_selector import (
    LLMEvidenceSelector,
)
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


DEFAULT_LLM_SELECTOR_MODEL = "deepseek-v4-flash"
# 2026-08-15 的 DeepSeek V4 Flash 缓存未命中单价；CLI 允许显式覆盖。
DEFAULT_FLASH_INPUT_PRICE_PER_MILLION_USD = 0.14
DEFAULT_FLASH_OUTPUT_PRICE_PER_MILLION_USD = 0.28


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
        "--similarity-threshold",
        type=float,
        default=None,
        help="仅用于评测实验的相似度阈值覆盖；默认读取项目配置",
    )
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
    parser.add_argument(
        "--evidence-selector",
        choices=("threshold", "llm"),
        default="threshold",
        help="证据选择策略；默认 threshold 不产生 LLM 调用费用",
    )
    parser.add_argument(
        "--llm-model",
        default=DEFAULT_LLM_SELECTOR_MODEL,
        help="仅在 evidence-selector=llm 时使用的 DeepSeek 模型",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("mock", "deepseek"),
        default="mock",
        help="LLM Selector 默认使用 Mock；真实调用必须显式选择 deepseek",
    )
    parser.add_argument(
        "--llm-thinking",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="是否启用 DeepSeek 思考模式；证据分类默认关闭",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=float,
        default=60.0,
        help="单道题的证据判断超时秒数",
    )
    parser.add_argument(
        "--llm-input-price-per-million-usd",
        type=float,
        default=DEFAULT_FLASH_INPUT_PRICE_PER_MILLION_USD,
        help="每百万输入 Token 美元单价，用于估算费用",
    )
    parser.add_argument(
        "--llm-output-price-per-million-usd",
        type=float,
        default=DEFAULT_FLASH_OUTPUT_PRICE_PER_MILLION_USD,
        help="每百万输出 Token 美元单价，用于估算费用",
    )
    return parser


def build_evidence_selector(
    *,
    selector_name: str,
    llm_provider: str,
    llm_model: str,
    llm_thinking: bool,
    llm_timeout_seconds: float,
) -> tuple[BaseEvidenceSelector, LLMEvidenceUsageRecorder | None]:
    """组装显式选择的第二阶段；默认阈值路径绝不会创建真实 LLM Provider。"""
    if selector_name == "threshold":
        return ThresholdEvidenceSelector(), None
    if selector_name != "llm":
        raise ValueError("不支持的证据选择策略")

    normalized_model = (
        MockProvider.MODEL_NAME if llm_provider == "mock" else llm_model.strip()
    )
    if not normalized_model:
        raise ValueError("证据选择模型不能为空")
    if llm_provider not in {"mock", "deepseek"}:
        raise ValueError("不支持的证据选择 LLM Provider")
    recorder = LLMEvidenceUsageRecorder()
    provider = (
        MockProvider()
        if llm_provider == "mock"
        else DeepSeekProvider(thinking_enabled=llm_thinking)
    )
    selector = LLMEvidenceSelector(
        provider=provider,
        model_name=normalized_model,
        input_budget_tokens=settings.deepseek.available_input_tokens,
        timeout_seconds=llm_timeout_seconds,
        telemetry_sink=recorder.record,
    )
    return selector, recorder


async def execute_evaluation(
    *,
    cases: tuple[RagEvaluationCase, ...],
    user_id: UUID,
    knowledge_base_id: UUID,
    top_k: int,
    similarity_threshold: float | None = None,
    evidence_selector: BaseEvidenceSelector | None = None,
) -> RagEvaluationReport:
    """在一个只读数据库 Session 中组装在线检索 Service 并运行评测。"""
    async with SessionLocal() as db:
        search_service = KnowledgeSearchService(
            db=db,
            embedding_factory=EmbeddingProviderFactory(),
            similarity_threshold=similarity_threshold,
            candidate_multiplier=settings.rag.search_candidate_multiplier,
            evidence_selector=evidence_selector,
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
        if (
            args.llm_input_price_per_million_usd < 0
            or args.llm_output_price_per_million_usd < 0
        ):
            raise ValueError("证据选择模型价格不能小于零")
        evidence_selector, usage_recorder = build_evidence_selector(
            selector_name=args.evidence_selector,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_thinking=args.llm_thinking,
            llm_timeout_seconds=args.llm_timeout_seconds,
        )
        # LLM Selector 自己判断答案充分性，不应在它之前应用旧向量阈值。
        effective_similarity_threshold = (
            -1.0
            if args.evidence_selector == "llm"
            and args.similarity_threshold is None
            else args.similarity_threshold
        )
        report = run_async(
            execute_evaluation(
                cases=cases,
                user_id=args.user_id,
                knowledge_base_id=args.knowledge_base_id,
                top_k=args.top_k,
                similarity_threshold=effective_similarity_threshold,
                evidence_selector=evidence_selector,
            )
        )
        selector_usage = (
            usage_recorder.build_summary(
                provider=args.llm_provider,
                expected_model=(
                    MockProvider.MODEL_NAME
                    if args.llm_provider == "mock"
                    else args.llm_model
                ),
                thinking_enabled=(
                    args.llm_thinking if args.llm_provider == "deepseek" else False
                ),
                input_price_per_million_usd=(
                    args.llm_input_price_per_million_usd
                    if args.llm_provider == "deepseek"
                    else 0.0
                ),
                output_price_per_million_usd=(
                    args.llm_output_price_per_million_usd
                    if args.llm_provider == "deepseek"
                    else 0.0
                ),
            )
            if usage_recorder is not None
            else None
        )
        report = report.model_copy(
            update={
                "retrieval_strategy": "vector",
                "evidence_selector": args.evidence_selector,
                "selector_usage": selector_usage,
            }
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
        RagSelectorUsageError,
        ValueError,
    ) as exc:
        parser.exit(status=1, message=f"RAG 评测失败：{exc}\n")

    print(f"RAG 评测完成：{report.total_cases} 个样例")
    print(f"JSON 报告：{artifacts.json_path.resolve()}")
    print(f"Markdown 报告：{artifacts.markdown_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
