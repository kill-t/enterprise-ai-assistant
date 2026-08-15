from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.core.config import ModelContextSettings, RagSettings
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.modules.conversation.service import conversation_knowledge_search_service
from app.modules.knowledge import dependencies as knowledge_dependencies
from app.modules.knowledge.retrieval.evidence_selection import (
    ThresholdEvidenceSelector,
)
from app.modules.knowledge.retrieval.evidence_selector_factory import (
    create_configured_evidence_selector,
)
from app.modules.knowledge.retrieval.llm_evidence_selector import (
    LLMEvidenceSelector,
)
from app.modules.knowledge.service.knowledge_search_factory import (
    create_knowledge_search_service,
)


def test_rag_selector_configuration_defaults_to_free_threshold() -> None:
    """默认配置必须保持阈值实现，启动和普通请求都不需要 DeepSeek 密钥。"""
    rag_settings = RagSettings()

    rag_settings.validate_runtime(deepseek_api_key="")

    assert rag_settings.evidence_selector == "threshold"
    assert rag_settings.llm_evidence_selector_model == "deepseek-v4-flash"
    assert rag_settings.llm_evidence_selector_thinking_enabled is False


def test_llm_selector_configuration_requires_runtime_key_and_valid_model() -> None:
    """显式启用真实 LLM 时应在启动阶段发现缺失密钥或空模型名。"""
    rag_settings = RagSettings(evidence_selector="llm")

    with pytest.raises(RuntimeError, match="DEEPSEEK__API_KEY"):
        rag_settings.validate_runtime(deepseek_api_key="")
    with pytest.raises(RuntimeError, match="DEEPSEEK__API_KEY"):
        rag_settings.validate_runtime(deepseek_api_key="replace-with-deepseek-key")

    rag_settings.validate_runtime(deepseek_api_key="sk-test-key")

    with pytest.raises(ValidationError, match="模型名"):
        RagSettings(llm_evidence_selector_model="   ")


def test_threshold_factory_never_constructs_llm_provider() -> None:
    """阈值模式必须直接返回本地实现，不能因为工厂导入而创建远程客户端。"""
    provider_factory = Mock(side_effect=AssertionError("不应创建 DeepSeek Provider"))

    selector = create_configured_evidence_selector(
        rag_settings=RagSettings(evidence_selector="threshold"),
        context_settings=ModelContextSettings(),
        llm_provider_factory=provider_factory,
    )

    assert isinstance(selector, ThresholdEvidenceSelector)
    provider_factory.assert_not_called()


def test_llm_factory_passes_model_budget_timeout_and_retry_configuration() -> None:
    """LLM 灰度参数必须真实进入 Selector，不能只停留在配置对象中。"""
    provider = MockProvider()
    provider_factory = Mock(return_value=provider)
    rag_settings = RagSettings(
        evidence_selector="llm",
        llm_evidence_selector_model="deepseek-v4-flash",
        llm_evidence_selector_timeout_seconds=12.5,
        llm_evidence_selector_thinking_enabled=False,
        llm_evidence_selector_max_output_retries=2,
    )
    context_settings = ModelContextSettings(
        context_window_tokens=10_000,
        max_output_tokens=1_000,
        token_safety_margin=500,
    )

    selector = create_configured_evidence_selector(
        rag_settings=rag_settings,
        context_settings=context_settings,
        llm_provider_factory=provider_factory,
    )

    assert isinstance(selector, LLMEvidenceSelector)
    assert selector.provider is provider
    assert selector.model_name == "deepseek-v4-flash"
    assert selector.input_budget_tokens == 8_500
    assert selector.timeout_seconds == 12.5
    assert selector.max_output_retries == 2
    provider_factory.assert_called_once_with(False)


def test_search_api_dependency_delegates_to_shared_service_factory(monkeypatch) -> None:
    """知识库搜索 API 必须使用共享工厂，避免自己固定某一种 Selector。"""
    service = object()
    factory = Mock(return_value=service)
    monkeypatch.setattr(
        knowledge_dependencies,
        "create_knowledge_search_service",
        factory,
    )
    db = object()

    result = knowledge_dependencies.get_knowledge_search_service(db=db)

    assert result is service
    factory.assert_called_once_with(db=db)


def test_shared_search_factory_disables_old_threshold_only_in_llm_mode() -> None:
    """LLM 模式应宽召回，阈值模式仍按知识库 Embedding 配置动态取阈值。"""
    selector = ThresholdEvidenceSelector()
    threshold_service = create_knowledge_search_service(
        db=object(),
        rag_settings=RagSettings(evidence_selector="threshold"),
        evidence_selector=selector,
    )
    llm_service = create_knowledge_search_service(
        db=object(),
        rag_settings=RagSettings(evidence_selector="llm"),
        evidence_selector=selector,
    )

    assert threshold_service.similarity_threshold is None
    assert llm_service.similarity_threshold == -1.0
    assert threshold_service.candidate_multiplier == 4
    assert llm_service.candidate_multiplier == 4


def test_conversation_rag_delegates_to_same_search_service_factory(monkeypatch) -> None:
    """聊天 RAG 默认路径必须复用共享工厂，不能悄悄退回 Service 内置阈值。"""
    core_service = object()
    factory = Mock(return_value=core_service)
    monkeypatch.setattr(
        conversation_knowledge_search_service,
        "create_knowledge_search_service",
        factory,
    )
    db = object()

    service = conversation_knowledge_search_service.ConversationKnowledgeSearchService(
        db=db
    )

    assert service.knowledge_search_service is core_service
    factory.assert_called_once_with(db=db)
