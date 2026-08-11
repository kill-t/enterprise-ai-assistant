"""执行现有知识检索 Service，并把结果转换为离线评测观测。"""

import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.core.loggers import app_logger
from app.modules.knowledge.evaluation.metrics import RagMetricsEvaluator
from app.modules.knowledge.evaluation.models import (
    RagEvaluationCase,
    RagEvaluationObservation,
    RagEvaluationReport,
    RetrievedEvidence,
)
from app.modules.knowledge.schema.knowledge_search_schema import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)


class KnowledgeSearchProtocol(Protocol):
    """声明 Runner 依赖的最小检索接口，便于测试注入和未来替换实现。"""

    async def search(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        request: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResponse:
        """返回经过权限、状态和相似度阈值过滤的来源。"""
        ...


class RagEvaluationRunError(RuntimeError):
    """表示某个样例执行失败或检索 Service 返回了不一致结果。"""


class RagRetrievalEvaluationRunner:
    """串行运行评测问题并生成不包含虚假回答指标的检索报告。"""

    def __init__(
        self,
        *,
        search_service: KnowledgeSearchProtocol,
        evaluator: RagMetricsEvaluator | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """注入现有检索 Service、指标计算器和可测试的单调时钟。"""
        self.search_service = search_service
        self.evaluator = evaluator or RagMetricsEvaluator()
        self.clock = clock

    async def run(
        self,
        *,
        cases: tuple[RagEvaluationCase, ...],
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> RagEvaluationReport:
        """按数据集顺序执行检索，任何单题失败都终止整批评测。"""
        observations: list[RagEvaluationObservation] = []
        for case in cases:
            observations.append(
                await self._run_case(
                    case=case,
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                )
            )
        return self.evaluator.evaluate(cases, tuple(observations))

    async def _run_case(
        self,
        *,
        case: RagEvaluationCase,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> RagEvaluationObservation:
        """执行单题并只记录安全标识、来源快照和检索耗时。"""
        started_at = self.clock()
        try:
            response = await self.search_service.search(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                request=KnowledgeSearchRequest(
                    query=case.question,
                    top_k=self.evaluator.top_k,
                ),
            )
        except Exception as exc:
            app_logger.warning(
                "RAG 检索评测样例执行失败 case_id=%s reason=%s",
                case.case_id,
                type(exc).__name__,
            )
            raise RagEvaluationRunError(
                f"RAG 检索评测样例执行失败：{case.case_id}"
            ) from exc

        latency_ms = max(0.0, (self.clock() - started_at) * 1000)
        if response.knowledge_base_id != knowledge_base_id:
            raise RagEvaluationRunError(
                f"RAG 检索评测返回了错误知识库：{case.case_id}"
            )

        sources = tuple(
            RetrievedEvidence(
                source_id=str(hit.chunk_id),
                filename=hit.filename,
                content=hit.content,
                score=hit.score,
            )
            for hit in response.results
        )
        app_logger.info(
            "RAG 检索评测样例完成 case_id=%s retrieved=%s latency_ms=%.2f",
            case.case_id,
            len(sources),
            latency_ms,
        )
        return RagEvaluationObservation(
            case_id=case.case_id,
            retrieved_sources=sources,
            refused=not sources,
            # 检索 Runner 没有运行回答模型，必须保留 None 表示引用指标未评测。
            cited_source_ids=None,
            latency_ms=latency_ms,
        )
