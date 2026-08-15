"""集中组装使用 PostgreSQL Recorder 的在线审计 Provider。"""

from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.provider_factory import ProviderFactory
from app.modules.llm_audit.audited_provider import AuditedChatProvider
from app.modules.llm_audit.recorder import DatabaseLLMRunRecorder


def create_audited_provider(
    *,
    provider: BaseChatProvider,
    provider_name: str,
    default_model: str,
) -> AuditedChatProvider:
    """为指定原始 Provider 增加统一的 PostgreSQL 审计能力。"""
    return AuditedChatProvider(
        provider=provider,
        recorder=DatabaseLLMRunRecorder(),
        provider_name=provider_name,
        default_model=default_model,
    )


def create_audited_default_provider() -> AuditedChatProvider:
    """创建当前默认聊天 Provider，并根据实际配置标注厂商和模型。"""
    return create_audited_provider(
        provider=ProviderFactory.create(),
        provider_name=settings.default_provider,
        default_model=ProviderFactory.get_model_name(),
    )
