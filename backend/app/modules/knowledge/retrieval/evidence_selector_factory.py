"""根据 RAG 灰度配置创建唯一的在线 EvidenceSelector 实现。"""

from collections.abc import Callable

from app.core.config import ModelContextSettings, RagSettings, settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.providers.deepseek_provider import DeepSeekProvider
from app.modules.knowledge.retrieval.evidence_selection import (
    BaseEvidenceSelector,
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.retrieval.llm_evidence_selector import (
    LLMEvidenceSelector,
)


def _create_deepseek_provider(thinking_enabled: bool) -> BaseChatProvider:
    """为在线 Selector 创建 DeepSeek Provider，并显式固定思考模式。"""
    return DeepSeekProvider(thinking_enabled=thinking_enabled)


def create_configured_evidence_selector(
    *,
    rag_settings: RagSettings | None = None,
    context_settings: ModelContextSettings | None = None,
    llm_provider_factory: Callable[[bool], BaseChatProvider] = (
        _create_deepseek_provider
    ),
) -> BaseEvidenceSelector:
    """默认返回阈值实现；显式启用 LLM 后才创建远程 Provider。"""
    resolved_rag_settings = rag_settings or settings.rag
    if resolved_rag_settings.evidence_selector == "threshold":
        return ThresholdEvidenceSelector()

    resolved_context_settings = context_settings or settings.deepseek
    provider = llm_provider_factory(
        resolved_rag_settings.llm_evidence_selector_thinking_enabled
    )
    return LLMEvidenceSelector(
        provider=provider,
        model_name=resolved_rag_settings.llm_evidence_selector_model,
        input_budget_tokens=resolved_context_settings.available_input_tokens,
        timeout_seconds=(
            resolved_rag_settings.llm_evidence_selector_timeout_seconds
        ),
        max_output_retries=(
            resolved_rag_settings.llm_evidence_selector_max_output_retries
        ),
    )
