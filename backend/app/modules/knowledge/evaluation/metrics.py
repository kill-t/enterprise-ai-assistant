"""用纯函数式流程计算 RAG 检索、拒答和引用指标。"""

import math
from statistics import fmean

from app.modules.knowledge.evaluation.models import (
    ExpectedEvidence,
    RagCaseEvaluationResult,
    RagEvaluationCase,
    RagEvaluationObservation,
    RagEvaluationReport,
    RagExpectedBehavior,
    RetrievedEvidence,
)


class RagEvaluationError(ValueError):
    """表示评测输入不完整、包含未知样例或标识重复。"""


class RagMetricsEvaluator:
    """按固定 Top-K 计算逐题明细和整批宏平均指标。"""

    def __init__(self, *, top_k: int = 5) -> None:
        """校验 Top-K，避免评测配置与在线检索上限不一致。"""
        if not 1 <= top_k <= 20:
            raise RagEvaluationError("RAG 评测 top_k 必须在 1 到 20 之间")
        self.top_k = top_k

    def evaluate(
        self,
        cases: tuple[RagEvaluationCase, ...],
        observations: tuple[RagEvaluationObservation, ...],
    ) -> RagEvaluationReport:
        """要求样例与观测一一对应，再按样例顺序生成稳定报告。"""
        if not cases:
            raise RagEvaluationError("RAG 评测至少需要一个样例")

        case_by_id = self._index_unique_cases(cases)
        observation_by_id = self._index_unique_observations(observations)
        unexpected_ids = observation_by_id.keys() - case_by_id.keys()
        if unexpected_ids:
            unexpected = ", ".join(sorted(unexpected_ids))
            raise RagEvaluationError(f"评测结果包含未知 case_id：{unexpected}")

        missing_ids = case_by_id.keys() - observation_by_id.keys()
        if missing_ids:
            missing = ", ".join(sorted(missing_ids))
            raise RagEvaluationError(f"评测结果缺少 case_id：{missing}")

        results = tuple(
            self._evaluate_case(case, observation_by_id[case.case_id])
            for case in cases
        )
        answer_results = [
            result for result in results if result.recall_at_k is not None
        ]
        answer_cases = len(answer_results)
        refusal_cases = len(cases) - answer_cases
        citation_results = [
            result
            for result in results
            if result.citation_valid is not None
        ]
        latencies = sorted(
            result.latency_ms
            for result in results
            if result.latency_ms is not None
        )
        embedding_model, similarity_threshold = self._resolve_search_metadata(
            observations
        )

        return RagEvaluationReport(
            embedding_model=embedding_model,
            similarity_threshold=similarity_threshold,
            top_k=self.top_k,
            total_cases=len(cases),
            answer_cases=answer_cases,
            refusal_cases=refusal_cases,
            recall_at_k=(
                fmean(result.recall_at_k for result in answer_results)
                if answer_results
                else None
            ),
            mean_reciprocal_rank=(
                fmean(result.reciprocal_rank for result in answer_results)
                if answer_results
                else None
            ),
            refusal_accuracy=fmean(result.refusal_correct for result in results),
            citation_validity_rate=(
                fmean(result.citation_valid for result in citation_results)
                if citation_results
                else None
            ),
            average_latency_ms=fmean(latencies) if latencies else None,
            p95_latency_ms=self._nearest_rank_percentile(latencies, 0.95),
            results=results,
        )

    @staticmethod
    def _resolve_search_metadata(
        observations: tuple[RagEvaluationObservation, ...],
    ) -> tuple[str | None, float | None]:
        """要求整批观测使用同一模型和阈值，避免把不同配置混成一个基线。"""
        models = [observation.embedding_model for observation in observations]
        thresholds = [
            observation.similarity_threshold for observation in observations
        ]
        if any(value is None for value in models) != all(
            value is None for value in models
        ):
            raise RagEvaluationError("RAG 评测观测的 Embedding 模型元数据不完整")
        if any(value is None for value in thresholds) != all(
            value is None for value in thresholds
        ):
            raise RagEvaluationError("RAG 评测观测的相似度阈值元数据不完整")

        known_models = {value for value in models if value is not None}
        known_thresholds = {value for value in thresholds if value is not None}
        if len(known_models) > 1:
            raise RagEvaluationError("同一份 RAG 报告不能混用多个 Embedding 模型")
        if len(known_thresholds) > 1:
            raise RagEvaluationError("同一份 RAG 报告不能混用多个相似度阈值")
        return (
            next(iter(known_models), None),
            next(iter(known_thresholds), None),
        )

    def _evaluate_case(
        self,
        case: RagEvaluationCase,
        observation: RagEvaluationObservation,
    ) -> RagCaseEvaluationResult:
        """计算一个问题的证据命中、首条相关排名、拒答和引用规则。"""
        should_refuse = case.expected_behavior == RagExpectedBehavior.REFUSE
        refusal_correct = observation.refused == should_refuse
        citation_valid = self._is_citation_valid(observation)

        if should_refuse:
            return RagCaseEvaluationResult(
                case_id=case.case_id,
                refusal_correct=refusal_correct,
                citation_valid=citation_valid,
                matched_evidence_count=0,
                expected_evidence_count=0,
                latency_ms=observation.latency_ms,
            )

        top_sources = observation.retrieved_sources[: self.top_k]
        matched_count = sum(
            any(self._matches(evidence, source) for source in top_sources)
            for evidence in case.expected_evidence
        )
        first_relevant_rank = next(
            (
                rank
                for rank, source in enumerate(top_sources, start=1)
                if any(
                    self._matches(evidence, source)
                    for evidence in case.expected_evidence
                )
            ),
            None,
        )

        return RagCaseEvaluationResult(
            case_id=case.case_id,
            recall_at_k=matched_count / len(case.expected_evidence),
            reciprocal_rank=(
                1 / first_relevant_rank if first_relevant_rank is not None else 0.0
            ),
            refusal_correct=refusal_correct,
            citation_valid=citation_valid,
            matched_evidence_count=matched_count,
            expected_evidence_count=len(case.expected_evidence),
            latency_ms=observation.latency_ms,
        )

    @staticmethod
    def _matches(
        expected: ExpectedEvidence,
        retrieved: RetrievedEvidence,
    ) -> bool:
        """文件名一致且原文片段被 Chunk 包含时，才认为标准证据被召回。"""
        return (
            expected.filename.casefold() == retrieved.filename.casefold()
            and expected.content_contains.casefold() in retrieved.content.casefold()
        )

    @staticmethod
    def _is_citation_valid(observation: RagEvaluationObservation) -> bool | None:
        """只在回答层实际运行后判断引用；纯检索观测返回不适用。"""
        if observation.cited_source_ids is None:
            return None
        if observation.refused:
            return not observation.cited_source_ids

        retrieved_ids = {
            source.source_id for source in observation.retrieved_sources
        }
        cited_ids = set(observation.cited_source_ids)
        return bool(cited_ids) and cited_ids.issubset(retrieved_ids)

    @staticmethod
    def _nearest_rank_percentile(
        sorted_values: list[float],
        percentile: float,
    ) -> float | None:
        """使用最近秩法计算百分位，少量样例时也能得到可解释的真实观测值。"""
        if not sorted_values:
            return None
        rank = max(1, math.ceil(percentile * len(sorted_values)))
        return sorted_values[rank - 1]

    @staticmethod
    def _index_unique_cases(
        cases: tuple[RagEvaluationCase, ...],
    ) -> dict[str, RagEvaluationCase]:
        """建立样例索引，并拒绝绕过 JSONL 加载器传入的重复 ID。"""
        indexed = {case.case_id: case for case in cases}
        if len(indexed) != len(cases):
            raise RagEvaluationError("RAG 评测样例包含重复的 case_id")
        return indexed

    @staticmethod
    def _index_unique_observations(
        observations: tuple[RagEvaluationObservation, ...],
    ) -> dict[str, RagEvaluationObservation]:
        """建立观测索引，确保每个问题只贡献一次指标。"""
        indexed = {observation.case_id: observation for observation in observations}
        if len(indexed) != len(observations):
            raise RagEvaluationError("RAG 评测结果包含重复的 case_id")
        return indexed
