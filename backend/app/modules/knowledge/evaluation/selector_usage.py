"""汇总 LLM 证据选择器的 Token、延迟和保守费用估计。"""

import math
from statistics import fmean

from app.modules.knowledge.evaluation.models import RagSelectorUsageSummary
from app.modules.knowledge.retrieval.llm_evidence_selector import (
    LLMEvidenceCallMetrics,
)


class RagSelectorUsageError(ValueError):
    """表示评测实际模型漂移或价格参数无法形成可信报告。"""


class LLMEvidenceUsageRecorder:
    """在内存中收集不含问题正文的单次 LLM 调用指标。"""

    def __init__(self) -> None:
        """初始化空指标列表；一个 Recorder 只服务于一批串行评测。"""
        self._metrics: list[LLMEvidenceCallMetrics] = []

    def record(self, metrics: LLMEvidenceCallMetrics) -> None:
        """按评测执行顺序追加一次调用，不复制 Prompt 或候选资料。"""
        self._metrics.append(metrics)

    def build_summary(
        self,
        *,
        provider: str,
        expected_model: str,
        thinking_enabled: bool,
        input_price_per_million_usd: float,
        output_price_per_million_usd: float,
    ) -> RagSelectorUsageSummary:
        """核对实际模型，并按缓存未命中价格计算可复算的费用上界估计。"""
        normalized_provider = provider.strip()
        normalized_model = expected_model.strip()
        if not normalized_provider or not normalized_model:
            raise RagSelectorUsageError("证据选择 Provider 和模型不能为空")
        if input_price_per_million_usd < 0 or output_price_per_million_usd < 0:
            raise RagSelectorUsageError("证据选择模型价格不能小于零")

        actual_models = {item.model for item in self._metrics}
        if actual_models and actual_models != {normalized_model}:
            raise RagSelectorUsageError("证据选择接口实际返回的模型与评测配置不一致")

        prompt_tokens = sum(item.prompt_tokens for item in self._metrics)
        completion_tokens = sum(item.completion_tokens for item in self._metrics)
        total_tokens = sum(item.total_tokens for item in self._metrics)
        latencies = sorted(item.latency_ms for item in self._metrics)
        estimated_cost_usd = (
            prompt_tokens * input_price_per_million_usd
            + completion_tokens * output_price_per_million_usd
        ) / 1_000_000
        return RagSelectorUsageSummary(
            provider=normalized_provider,
            model=normalized_model,
            thinking_enabled=thinking_enabled,
            calls=len(self._metrics),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            average_latency_ms=fmean(latencies) if latencies else None,
            p95_latency_ms=self._nearest_rank_percentile(latencies, 0.95),
            input_price_per_million_usd=input_price_per_million_usd,
            output_price_per_million_usd=output_price_per_million_usd,
            estimated_cost_usd=estimated_cost_usd,
        )

    @staticmethod
    def _nearest_rank_percentile(
        sorted_values: list[float],
        percentile: float,
    ) -> float | None:
        """使用与 RAG 主报告一致的最近秩法计算 P95。"""
        if not sorted_values:
            return None
        rank = max(1, math.ceil(percentile * len(sorted_values)))
        return sorted_values[rank - 1]
